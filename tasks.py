"""Shared card tasks, optimistic edits, and durable history. No bot delivery here."""
import json
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

import models
from database import get_db

DEPARTMENTS = "Отделы и направления"
EVENTS = "Мероприятия"


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def display_name(user):
    return " ".join(filter(None, [user.last_name, user.first_name, user.middle_name])) or user.login


def can_delete_tasks(position):
    name = (position.name or "").strip().lower()
    return (name in {"председатель", "председатель студсовета"}
            or name.startswith("заместитель председателя")
            or name.startswith("зам. председателя"))


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=20000)
    deadline: Optional[date] = None
    event_card_id: Optional[int] = Field(default=None, gt=0)
    department_card_ids: list[int] = Field(default_factory=list, max_length=100)
    assignee_ids: list[int] = Field(default_factory=list, max_length=100)
    completed: bool = False

    @field_validator("title")
    @classmethod
    def nonempty(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Введите название задачи")
        return value

    @field_validator("department_card_ids", "assignee_ids")
    @classmethod
    def valid_ids(cls, value):
        if any(x <= 0 for x in value):
            raise ValueError("Некорректный идентификатор")
        return sorted(set(value))


class TaskPatch(TaskCreate):
    title: Optional[str] = Field(default=None, min_length=1, max_length=300)
    description: Optional[str] = Field(default=None, max_length=20000)
    department_card_ids: Optional[list[int]] = Field(default=None, max_length=100)
    assignee_ids: Optional[list[int]] = Field(default=None, max_length=100)
    completed: Optional[bool] = None
    version: int = Field(gt=0)

    @field_validator("title", "description", "department_card_ids", "assignee_ids", "completed", mode="before")
    @classmethod
    def no_null(cls, value):
        if value is None:
            raise ValueError("Поле не может быть пустым")
        return value


class TaskVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(gt=0)


def begin_task_write(db):
    """Serialize link validation with card deletion/category changes across workers."""
    if db.bind.dialect.name == 'sqlite':
        # Auth has only read through this Session; acquire the write reservation
        # BEFORE reading linked cards. Released by commit/rollback/session close.
        db.connection().exec_driver_sql('BEGIN IMMEDIATE')
    else:
        db.query(models.Category).order_by(models.Category.id).with_for_update().all()

def assert_card_has_no_tasks(db, card_id):
    linked = db.query(models.Task.id).filter(or_(
        models.Task.event_card_id == card_id,
        models.Task.department_links.any(models.TaskDepartment.card_id == card_id),
    )).first()
    if linked:
        raise HTTPException(409, "Карточка связана с задачами. Сначала перенесите связи задач, включая архивные.")


def create_tasks_router(*, get_current_user, has_access_to_card):
    router = APIRouter(prefix="/api/tasks", tags=["Tasks"])

    def card_visible(card, user):
        return card is not None and has_access_to_card(user, card)

    def accessible(task, user):
        return ((task.event_card_id is None or card_visible(task.event_card, user))
                and all(card_visible(link.card, user) for link in task.department_links))

    def query(db):
        return db.query(models.Task).options(
            selectinload(models.Task.event_card),
            selectinload(models.Task.department_links).selectinload(models.TaskDepartment.card),
            selectinload(models.Task.assignee_links).selectinload(models.TaskAssignee.user),
        )

    def get_task(db, task_id, user):
        task = query(db).filter(models.Task.id == task_id).first()
        if task is None or not accessible(task, user):
            raise HTTPException(404, "Задача не найдена или недоступна")
        return task

    def card_data(card):
        return {"id": card.id, "title": card.title}

    def serialize(task):
        departments = sorted([card_data(link.card) for link in task.department_links], key=lambda x: x['id'])
        assignees = sorted([{"id": link.user.id, "name": display_name(link.user),
                             "login": link.user.login,
                             "active": bool(link.user.is_active and not link.user.is_deactivated)}
                            for link in task.assignee_links], key=lambda x: x['id'])
        return {"id": task.id, "title": task.title, "description": task.description,
                "deadline": task.deadline, "created_at": task.created_at.isoformat() + "Z",
                "updated_at": task.updated_at.isoformat() + "Z", "creator_id": task.creator_id,
                "event_card_id": task.event_card_id,
                "event": card_data(task.event_card) if task.event_card else None,
                "departments": departments, "department_card_ids": [x['id'] for x in departments],
                "assignees": assignees, "assignee_ids": [x['id'] for x in assignees],
                "completed": task.completed, "archived": task.archived, "version": task.version}

    def validate_links(db, data, user, task=None):
        if data.get("event_card_id") is not None:
            card = db.get(models.Card, data["event_card_id"])
            if not card_visible(card, user):
                raise HTTPException(404, "Мероприятие не найдено или недоступно")
            if card.category.name != EVENTS:
                raise HTTPException(422, "Выберите карточку мероприятия")
        for card_id in data.get("department_card_ids", []):
            card = db.get(models.Card, card_id)
            if not card_visible(card, user):
                raise HTTPException(404, "Отдел не найден или недоступен")
            if card.category.name != DEPARTMENTS:
                raise HTTPException(422, "Выберите карточки отделов и направлений")
        previous = {link.user_id for link in task.assignee_links} if task else set()
        for user_id in data.get("assignee_ids", []):
            assignee = db.get(models.User, user_id)
            if assignee is None or (user_id not in previous and (not assignee.is_active or assignee.is_deactivated)):
                raise HTTPException(422, "Ответственный не найден или аккаунт отключён")

    def apply(task, data, db):
        for key, value in data.items():
            if key == "department_card_ids":
                existing = {link.card_id: link for link in task.department_links}
                task.department_links = [existing.get(cid) or models.TaskDepartment(card_id=cid, card=db.get(models.Card, cid))
                                         for cid in value]
            elif key == "assignee_ids":
                existing = {link.user_id: link for link in task.assignee_links}
                task.assignee_links = [existing.get(uid) or models.TaskAssignee(user_id=uid, user=db.get(models.User, uid))
                                       for uid in value]
            elif key == "event_card_id":
                task.event_card_id = value
                task.event_card = db.get(models.Card, value) if value else None
            elif key == "deadline":
                task.deadline = value.isoformat() if value else None
            else:
                setattr(task, key, value)

    def history(db, task, actor, action, before):
        db.add(models.TaskHistory(task_id=task.id, actor_id=actor.id, actor_name=display_name(actor),
            action=action, before_json=json.dumps(before, ensure_ascii=False) if before else None,
            after_json=json.dumps(serialize(task), ensure_ascii=False), created_at=now()))

    def commit(db):
        try:
            db.commit()
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Задача уже изменена. Обновите данные и повторите действие.")

    @router.get("/options")
    def options(user=Depends(get_current_user), db: Session = Depends(get_db)):
        cards = db.query(models.Card).join(models.Category).filter(models.Category.name.in_([DEPARTMENTS, EVENTS])).all()
        users = db.query(models.User).filter(models.User.is_active.is_(True), models.User.is_deactivated.is_(False)).all()
        return {"departments": [card_data(c) for c in cards if c.category.name == DEPARTMENTS and card_visible(c, user)],
                "events": [card_data(c) for c in cards if c.category.name == EVENTS and card_visible(c, user)],
                "users": [{"id": u.id, "name": display_name(u), "login": u.login, "active": True}
                          for u in sorted(users, key=display_name)],
                "can_delete": can_delete_tasks(user.position_ref)}

    @router.get("")
    def list_tasks(card_id: Optional[int] = None, archived: bool = False,
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
        tasks = query(db).filter(models.Task.archived == archived)
        if card_id is not None:
            if not card_visible(db.get(models.Card, card_id), user):
                raise HTTPException(404, "Карточка не найдена или недоступна")
            tasks = tasks.filter(or_(models.Task.event_card_id == card_id,
                                     models.Task.department_links.any(models.TaskDepartment.card_id == card_id)))
        return [serialize(t) for t in tasks.order_by(models.Task.created_at.desc(), models.Task.id.desc()).all()
                if accessible(t, user)]

    @router.post("", status_code=201)
    def create_task(data: TaskCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
        begin_task_write(db)
        values = data.model_dump()
        validate_links(db, values, user)
        task = models.Task(creator_id=user.id, created_at=now(), updated_at=now(), archived=False)
        db.add(task)
        apply(task, values, db)
        db.flush()
        history(db, task, user, "create", None)
        commit(db)
        return serialize(task)

    @router.get("/{task_id}")
    def read_task(task_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
        return serialize(get_task(db, task_id, user))

    @router.patch("/{task_id}")
    def update_task(task_id: int, data: TaskPatch, user=Depends(get_current_user), db: Session = Depends(get_db)):
        begin_task_write(db)
        task = get_task(db, task_id, user)
        if task.archived or task.version != data.version:
            raise HTTPException(409, "Задача изменена или удалена. Обновите данные.")
        values = data.model_dump(exclude_unset=True, exclude={"version"})
        validate_links(db, values, user, task)
        before = serialize(task)
        apply(task, values, db)
        if serialize(task) == before:
            return before
        task.updated_at = now()
        try:
            db.flush()
            history(db, task, user, "update", before)
            commit(db)
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Задача уже изменена. Обновите данные.")
        return serialize(task)

    @router.delete("/{task_id}")
    def archive_task(task_id: int, data: TaskVersion, user=Depends(get_current_user), db: Session = Depends(get_db)):
        if not can_delete_tasks(user.position_ref):
            raise HTTPException(403, "Удалять задачи могут только председатель и заместители")
        begin_task_write(db)
        task = get_task(db, task_id, user)
        if task.archived or task.version != data.version:
            raise HTTPException(409, "Задача изменена или уже удалена. Обновите данные.")
        before = serialize(task)
        task.archived = True
        task.updated_at = now()
        try:
            db.flush()
            history(db, task, user, "archive", before)
            commit(db)
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Задача уже изменена. Обновите данные.")
        return serialize(task)

    def snapshot_visible(snapshot, user, db):
        if not snapshot:
            return True
        ids = list(snapshot.get("department_card_ids", []))
        if snapshot.get("event_card_id"):
            ids.append(snapshot["event_card_id"])
        return all(card_visible(db.get(models.Card, cid), user) for cid in ids)

    @router.get("/{task_id}/history")
    def read_history(task_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
        get_task(db, task_id, user)
        rows = db.query(models.TaskHistory).filter(models.TaskHistory.task_id == task_id).order_by(models.TaskHistory.id.desc()).all()
        result = []
        for row in rows:
            before = json.loads(row.before_json) if row.before_json else None
            after = json.loads(row.after_json)
            visible = snapshot_visible(before, user, db) and snapshot_visible(after, user, db)
            result.append({"id": row.id, "actor_id": row.actor_id, "actor_name": row.actor_name,
                           "action": row.action, "created_at": row.created_at.isoformat() + "Z",
                           "before": before if visible else None, "after": after if visible else None,
                           "restricted": not visible})
        return result

    return router
