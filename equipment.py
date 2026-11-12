"""Equipment custody and append-only history; member auth is injected by main."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

import models
from database import get_db


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LocationCreate(Input):
    name: str = Field(min_length=1, max_length=120)


class ItemCreate(Input):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    holder_user_id: Optional[int] = Field(default=None, gt=0)
    location_id: Optional[int] = Field(default=None, gt=0)
    taken_at: Optional[datetime] = None


class TakeItem(Input):
    version: int = Field(ge=1)


class ItemUpdate(TakeItem):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=5000)
    holder_user_id: Optional[int] = Field(default=None, gt=0)
    location_id: Optional[int] = Field(default=None, gt=0)
    taken_at: Optional[datetime] = None

    @field_validator("name", "description")
    @classmethod
    def non_null_text(cls, value):
        if value is None:
            raise ValueError("Текстовое поле не может быть null")
        return value


def full_name(user):
    return " ".join(part for part in (user.last_name, user.first_name, user.middle_name) if part)


def location_data(location):
    return {"id": location.id, "name": location.name}


def user_data(user):
    return {"id": user.id, "login": user.login, "name": full_name(user)}


def item_data(item):
    return {
        "id": item.id, "name": item.name, "description": item.description,
        "holder_user": user_data(item.holder_user) if item.holder_user else None,
        "location": location_data(item.location) if item.location else None,
        "taken_at": item.taken_at, "version": item.version,
    }


def default_location(db):
    # Idempotent under multiple workers and first-time concurrent requests.
    db.execute(insert(models.EquipmentLocation).values(name="в главке", name_key="в главке")
               .on_conflict_do_nothing(index_elements=["name_key"]))
    db.flush()
    return db.query(models.EquipmentLocation).filter_by(name_key="в главке").one()


def resolve_custody(db, user_id, location_id, taken_at):
    if (user_id is None) == (location_id is None):
        raise HTTPException(422, "Выберите одного пользователя или одно место хранения")
    if user_id is not None:
        user = db.get(models.User, user_id)
        if user is None or not user.is_active or user.is_deactivated:
            raise HTTPException(422, "Выберите действующего пользователя")
        if taken_at is None:
            raise HTTPException(422, "Укажите дату взятия предмета")
        if taken_at.tzinfo is not None:
            taken_at = taken_at.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        if db.get(models.EquipmentLocation, location_id) is None:
            raise HTTPException(422, "Место хранения не найдено")
        if taken_at is not None:
            raise HTTPException(422, "Дата взятия задаётся только для пользователя")
    return {"holder_user_id": user_id, "location_id": location_id, "taken_at": taken_at}


def record_history(db, item, actor, action):
    # Snapshot names and logins: later profile edits cannot rewrite past custody.
    db.add(models.EquipmentHistory(
        item_id=item.id, action=action, actor_name=full_name(actor), actor_login=actor.login,
        holder_name=full_name(item.holder_user) if item.holder_user else item.location.name,
        holder_login=item.holder_user.login if item.holder_user else None,
        taken_at=item.taken_at, recorded_at=now_utc(),
    ))


def create_equipment_router(*, get_current_user, can_manage, can_delete):
    router = APIRouter(prefix="/api/equipment", tags=["Equipment"])

    def manager(user=Depends(get_current_user)):
        if not can_manage(user.position_ref):
            raise HTTPException(403, "Редактирование оборудования доступно только руководству")
        return user

    def deleter(user=Depends(get_current_user)):
        if not can_delete(user.position_ref):
            raise HTTPException(403, "Удалять предметы могут только председатель, его заместители и админ")
        return user

    def find_item(db, item_id, include_deleted=False):
        item = db.get(models.EquipmentItem, item_id)
        if item is None or (item.deleted_at is not None and not include_deleted):
            raise HTTPException(404, "Предмет не найден")
        return item

    def save_update(db, item, values, version, actor, action=None):
        result = db.execute(update(models.EquipmentItem).where(
            models.EquipmentItem.id == item.id, models.EquipmentItem.version == version,
            models.EquipmentItem.deleted_at.is_(None),
        ).values(**values, version=version + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Предмет уже изменён. Обновите таблицу и повторите действие")
        db.expire_all()
        db.refresh(item)
        if action:
            record_history(db, item, actor, action)
        db.commit()
        db.refresh(item)
        return item_data(item)

    @router.get("")
    def list_items(_user=Depends(get_current_user), db: Session = Depends(get_db)):
        items = db.query(models.EquipmentItem).filter(models.EquipmentItem.deleted_at.is_(None)).options(
            joinedload(models.EquipmentItem.holder_user), joinedload(models.EquipmentItem.location),
        ).order_by(models.EquipmentItem.id).all()
        return [item_data(item) for item in items]

    @router.get("/options")
    def options(user=Depends(get_current_user), db: Session = Depends(get_db)):
        default = default_location(db)
        default_id = default.id
        db.commit()
        locations = db.query(models.EquipmentLocation).order_by(models.EquipmentLocation.id).all()
        users = db.query(models.User).filter(
            models.User.is_active.is_(True), models.User.is_deactivated.is_(False),
        ).order_by(models.User.last_name, models.User.first_name, models.User.id).all()
        return {"can_manage": can_manage(user.position_ref), "can_delete": can_delete(user.position_ref), "default_location_id": default_id,
                "locations": [location_data(value) for value in locations],
                "users": [user_data(value) for value in users]}

    @router.post("/locations", status_code=201)
    def create_location(data: LocationCreate, _user=Depends(manager), db: Session = Depends(get_db)):
        location = models.EquipmentLocation(name=data.name, name_key=data.name.casefold())
        db.add(location)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "Такое место хранения уже существует") from None
        db.refresh(location)
        return location_data(location)

    @router.post("", status_code=201)
    def create_item(data: ItemCreate, user=Depends(manager), db: Session = Depends(get_db)):
        location_id = data.location_id
        if data.holder_user_id is None and location_id is None:
            location_id = default_location(db).id
        taken_at = data.taken_at
        if data.holder_user_id is not None and taken_at is None:
            taken_at = now_utc()
        custody = resolve_custody(db, data.holder_user_id, location_id, taken_at)
        item = models.EquipmentItem(name=data.name, description=data.description, **custody)
        db.add(item)
        db.flush()
        record_history(db, item, user, "created")
        db.commit()
        db.refresh(item)
        return item_data(item)

    @router.patch("/{item_id}")
    def edit_item(item_id: int, data: ItemUpdate, user=Depends(manager), db: Session = Depends(get_db)):
        item = find_item(db, item_id)
        incoming = data.model_dump(exclude_unset=True, exclude={"version"})
        values = {key: incoming[key] for key in ("name", "description") if key in incoming}
        custody_keys = {"holder_user_id", "location_id", "taken_at"}
        custody_changed = False
        if custody_keys.intersection(incoming):
            user_id = incoming.get("holder_user_id", item.holder_user_id)
            location_id = incoming.get("location_id", item.location_id)
            if incoming.get("holder_user_id") is not None and "location_id" not in incoming:
                location_id = None
            if incoming.get("location_id") is not None and "holder_user_id" not in incoming:
                user_id = None
            holder_changed = (user_id, location_id) != (item.holder_user_id, item.location_id)
            default_date = (now_utc() if user_id is not None else None) if holder_changed else item.taken_at
            custody = resolve_custody(db, user_id, location_id, incoming.get("taken_at", default_date))
            custody_changed = holder_changed or custody["taken_at"] != item.taken_at
            values.update(custody)
        return save_update(db, item, values, data.version, user, "assigned" if custody_changed else None)

    @router.post("/{item_id}/take")
    def take_item(item_id: int, data: TakeItem, user=Depends(get_current_user), db: Session = Depends(get_db)):
        item = find_item(db, item_id)
        return save_update(db, item, {"holder_user_id": user.id, "location_id": None,
                                     "taken_at": now_utc()}, data.version, user, "taken")

    @router.delete("/{item_id}")
    def delete_item(item_id: int, data: TakeItem, actor=Depends(deleter), db: Session = Depends(get_db)):
        item = find_item(db, item_id)
        return save_update(db, item, {"deleted_at": now_utc()}, data.version, actor, "deleted")

    @router.get("/{item_id}/history")
    def history(item_id: int, _user=Depends(get_current_user), db: Session = Depends(get_db)):
        find_item(db, item_id, include_deleted=True)
        events = db.query(models.EquipmentHistory).filter_by(item_id=item_id).order_by(
            models.EquipmentHistory.id.desc()).all()
        return [{"id": event.id, "action": event.action, "actor_name": event.actor_name,
                 "actor_login": event.actor_login, "holder_name": event.holder_name,
                 "holder_login": event.holder_login, "taken_at": event.taken_at,
                 "recorded_at": event.recorded_at} for event in events]

    return router
