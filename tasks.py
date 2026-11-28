"""Shared card tasks, optimistic edits, and durable history. No bot delivery here."""
import json
from datetime import date, datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

import models
from database import get_db
from telegram_auth import replay, remember, no_store

DEPARTMENTS = "Отделы и направления"
EVENTS = "Мероприятия"


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def display_name(user):
    return " ".join(filter(None, [user.last_name, user.first_name, user.middle_name])) or user.login


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
            raise ValueError("Введите название таски")
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


class TaskPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


class TaskVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(gt=0)


class TaskReference(TaskVersion):
    id: int = Field(gt=0)


class TaskBulkArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[TaskReference] = Field(min_length=1, max_length=5000)


def begin_task_write(db):
    """Serialize link validation with card deletion/category changes across workers."""
    if db.bind.dialect.name == 'sqlite':
        # Auth has only read through this Session; acquire the write reservation
        # BEFORE reading linked cards. Released by commit/rollback/session close.
        connection = db.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
    else:
        db.query(models.Category).order_by(models.Category.id).with_for_update().all()

def assert_card_has_no_tasks(db, card_id):
    from responsibility_zones import assert_no_zones
    assert_no_zones(db, card_id)
    linked = db.query(models.Task.id).filter(or_(
        models.Task.event_card_id == card_id,
        models.Task.department_links.any(models.TaskDepartment.card_id == card_id),
    )).first()
    if linked:
        raise HTTPException(409, "Карточка связана с тасками. Сначала перенесите связи тасок, включая архивные.")


def create_tasks_router(*, get_current_user, has_access_to_card, can_delete_tasks, prefix="/api/tasks", bot_mode=False):
    router = APIRouter(prefix=prefix, tags=["Tasks"], dependencies=[Depends(no_store)] if bot_mode else [])

    def card_visible(card, user):
        return card is not None and has_access_to_card(user, card)

    def accessible(task, user):
        return ((task.event_card_id is None or card_visible(task.event_card, user))
                and all(card_visible(link.card, user) for link in task.department_links))

    def query(db):
        return db.query(models.Task).options(
            selectinload(models.Task.event_card),
            selectinload(models.Task.creator),
            selectinload(models.Task.department_links).selectinload(models.TaskDepartment.card),
            selectinload(models.Task.assignee_links).selectinload(models.TaskAssignee.user),
        )

    def participant(task, user):
        return task.creator_id == user.id or any(link.user_id == user.id for link in task.assignee_links)

    def get_task(db, task_id, user):
        task = query(db).filter(models.Task.id == task_id).first()
        if task is None or not accessible(task, user):
            raise HTTPException(404, "Таска не найдена или недоступна")
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
                "creator_name": display_name(task.creator),
                "event_card_id": task.event_card_id,
                "event": card_data(task.event_card) if task.event_card else None,
                "departments": departments, "department_card_ids": [x['id'] for x in departments],
                "assignees": assignees, "assignee_ids": [x['id'] for x in assignees],
                "completed": task.completed, "archived": task.archived, "version": task.version}

    def task_response(task, user):
        return {**serialize(task), "can_change_status": not task.archived and participant(task, user)}

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

    def history(db, task, actor, action, before, notification_bindings=None):
        after = serialize(task)
        record = models.TaskHistory(task_id=task.id, actor_id=actor.id, actor_name=display_name(actor),
            action=action, before_json=json.dumps(before, ensure_ascii=False) if before else None,
            after_json=json.dumps(after, ensure_ascii=False), created_at=now())
        db.add(record)
        from telegram_notifications import enqueue_assignment_changes
        enqueue_assignment_changes(db, task, before, after, actor, has_access_to_card, record, bindings=notification_bindings)

    def commit(db):
        try:
            db.commit()
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Таска уже изменена. Обновите данные и повторите действие.")

    def preference(scope, user, db):
        return {"enabled": db.get(models.TaskNotificationMute, (user.id, scope)) is None}

    def set_preference(scope, data, user, db):
        cached = replay(db, user)
        if cached is not None:
            return cached
        mute = db.get(models.TaskNotificationMute, (user.id, scope))
        if data.enabled and mute is not None:
            db.delete(mute)
        elif not data.enabled:
            if mute is None:
                db.add(models.TaskNotificationMute(user_id=user.id, scope=scope))
            pending = db.query(models.TelegramNotification).filter_by(user_id=user.id, state="pending")
            if scope:
                pending = pending.filter_by(task_id=scope)
            pending.update({"state": "cancelled"}, synchronize_session=False)
        result = {"enabled": data.enabled}
        remember(db, user, result)
        commit(db)
        return result

    @router.get("/preferences/notifications")
    def read_global_preference(user=Depends(get_current_user), db: Session = Depends(get_db)):
        return preference(0, user, db)

    @router.put("/preferences/notifications")
    def write_global_preference(data: TaskPreference, user=Depends(get_current_user), db: Session = Depends(get_db)):
        begin_task_write(db)
        return set_preference(0, data, user, db)

    @router.get("/{task_id}/preferences/notifications")
    def read_task_preference(task_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
        get_task(db, task_id, user)
        return preference(task_id, user, db)

    @router.put("/{task_id}/preferences/notifications")
    def write_task_preference(task_id: int, data: TaskPreference, user=Depends(get_current_user), db: Session = Depends(get_db)):
        begin_task_write(db)
        get_task(db, task_id, user)
        return set_preference(task_id, data, user, db)

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
                   assignee_id: Optional[int] = None, active_only: bool = False, include_created: bool = False,
                   include_completed: bool = False, scope: Literal["mine", "all"] = "mine",
                   user=Depends(get_current_user), db: Session = Depends(get_db)):
        if bot_mode and scope == "mine":
            assignee_id, active_only, archived = user.id, not include_completed, False
        tasks = query(db).filter(models.Task.archived == archived)
        if assignee_id is not None:
            assigned = models.Task.assignee_links.any(models.TaskAssignee.user_id == assignee_id)
            tasks = tasks.filter(or_(assigned, models.Task.creator_id == user.id) if bot_mode and scope == "mine" and include_created else assigned)
        if active_only:
            tasks = tasks.filter(models.Task.completed.is_(False))
        if card_id is not None:
            if not card_visible(db.get(models.Card, card_id), user):
                raise HTTPException(404, "Карточка не найдена или недоступна")
            tasks = tasks.filter(or_(models.Task.event_card_id == card_id,
                                     models.Task.department_links.any(models.TaskDepartment.card_id == card_id)))
        return [task_response(t, user) for t in tasks.order_by(models.Task.created_at.desc(), models.Task.id.desc()).all()
                if accessible(t, user)]

    @router.post("", status_code=201)
    def create_task(data: TaskCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
        begin_task_write(db)
        cached = replay(db, user)
        if cached is not None:
            get_task(db, cached["id"], user)
            return accessible_receipt(cached, user, db)
        values = data.model_dump()
        validate_links(db, values, user)
        task = models.Task(creator_id=user.id, created_at=now(), updated_at=now(), archived=False)
        if db.bind.dialect.name == "sqlite":
            sequence = db.get(models.TaskIdSequence, 1)
            if sequence is not None:
                current_max = db.query(func.max(models.Task.id)).scalar() or 0
                task.id = max(sequence.last_deleted_id, current_max) + 1
        db.add(task)
        apply(task, values, db)
        db.flush()
        history(db, task, user, "create", None)
        result = task_response(task, user)
        remember(db, user, result)
        commit(db)
        return result

    @router.post("/archive-completed")
    def archive_completed(data: TaskBulkArchive, user=Depends(get_current_user), db: Session = Depends(get_db)):
        if not can_delete_tasks(user.position_ref):
            raise HTTPException(403, "Недостаточно прав для переноса тасок в архив")
        begin_task_write(db)
        ids = [item.id for item in data.tasks]
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Таски не должны повторяться")
        tasks_by_id = {task.id: task for task in query(db).filter(models.Task.id.in_(ids)).all()}
        selected = []
        for item in data.tasks:
            task = tasks_by_id.get(item.id)
            if task is None or not accessible(task, user):
                raise HTTPException(404, "Таска не найдена или недоступна")
            if task.archived or not task.completed or task.version != item.version:
                raise HTTPException(409, "Список тасок изменился. Обновите страницу и повторите действие.")
            selected.append(task)
        recipients = {task.creator_id for task in selected}
        recipients.update(link.user_id for task in selected for link in task.assignee_links)
        recipients.discard(user.id)
        bindings = db.query(models.TelegramBinding).options(selectinload(models.TelegramBinding.user)).filter(
            models.TelegramBinding.active.is_(True), models.TelegramBinding.user_id.in_(recipients)).all() if recipients else []
        try:
            snapshots = [(task, serialize(task)) for task in selected]
            for task in selected:
                task.archived = True
                task.updated_at = now()
            db.flush()
            for task, before in snapshots:
                history(db, task, user, "archive", before, notification_bindings=bindings)
            commit(db)
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Список тасок изменился. Обновите страницу.")
        return {"count": len(selected)}

    @router.post("/{task_id}/restore")
    def restore_task(task_id: int, data: TaskVersion, user=Depends(get_current_user), db: Session = Depends(get_db)):
        if not can_delete_tasks(user.position_ref):
            raise HTTPException(403, "Недостаточно прав для восстановления таски")
        begin_task_write(db)
        task = get_task(db, task_id, user)
        if not task.archived or task.version != data.version:
            raise HTTPException(409, "Таска изменилась или уже восстановлена. Обновите страницу.")
        before = serialize(task)
        task.archived = False
        task.updated_at = now()
        try:
            db.flush()
            history(db, task, user, "restore", before)
            commit(db)
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Таска уже изменена. Обновите страницу.")
        return task_response(task, user)

    @router.get("/{task_id}")
    def read_task(task_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
        return task_response(get_task(db, task_id, user), user)

    @router.patch("/{task_id}")
    def update_task(task_id: int, data: TaskPatch, user=Depends(get_current_user), db: Session = Depends(get_db)):
        begin_task_write(db)
        task = get_task(db, task_id, user)
        cached = replay(db, user)
        if cached is not None:
            return accessible_receipt(cached, user, db)
        if task.archived or task.version != data.version:
            raise HTTPException(409, "Таска изменена или перенесена в архив. Обновите данные.")
        values = data.model_dump(exclude_unset=True, exclude={"version"})
        if "completed" in values and values["completed"] != task.completed and not participant(task, user):
            raise HTTPException(403, "Менять статус могут только создатель и ответственные")
        validate_links(db, values, user, task)
        before = serialize(task)
        apply(task, values, db)
        if serialize(task) == before:
            result = task_response(task, user)
            if bot_mode:
                remember(db, user, result)
                commit(db)
            return result
        task.updated_at = now()
        try:
            db.flush()
            history(db, task, user, "update", before)
            remember(db, user, task_response(task, user))
            commit(db)
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Таска уже изменена. Обновите данные.")
        return task_response(task, user)

    @router.delete("/{task_id}")
    def archive_task(task_id: int, data: TaskVersion, user=Depends(get_current_user), db: Session = Depends(get_db)):
        if not can_delete_tasks(user.position_ref):
            raise HTTPException(403, "Переносить таски в архив могут только админ, председатель и его заместители")
        begin_task_write(db)
        task = get_task(db, task_id, user)
        if task.archived or task.version != data.version:
            raise HTTPException(409, "Таска изменена или уже перенесена в архив. Обновите данные.")
        before = serialize(task)
        task.archived = True
        task.updated_at = now()
        try:
            db.flush()
            history(db, task, user, "archive", before)
            commit(db)
        except StaleDataError:
            db.rollback()
            raise HTTPException(409, "Таска уже изменена. Обновите данные.")
        return task_response(task, user)

    @router.delete("/{task_id}/permanent")
    def delete_archived(task_id: int, data: TaskVersion, user=Depends(get_current_user), db: Session = Depends(get_db)):
        if not can_delete_tasks(user.position_ref):
            raise HTTPException(403, "Удалять таски могут только админ, председатель и его заместители")
        begin_task_write(db)
        task = get_task(db, task_id, user)
        if not task.archived or task.version != data.version:
            raise HTTPException(409, "Для удаления выберите актуальную версию архивированной таски")
        if db.bind.dialect.name == "sqlite":
            sequence = db.get(models.TaskIdSequence, 1)
            if sequence is None:
                db.add(models.TaskIdSequence(id=1, last_deleted_id=task.id))
            else:
                sequence.last_deleted_id = max(sequence.last_deleted_id, task.id)
        db.query(models.TaskHistory).filter_by(task_id=task.id).delete()
        db.query(models.TelegramNotification).filter_by(task_id=task.id).delete()
        db.query(models.TaskNotificationMute).filter_by(scope=task.id).delete()
        db.delete(task)
        commit(db)
        return {"id": task_id}

    def snapshot_visible(snapshot, user, db):
        if not snapshot:
            return True
        ids = list(snapshot.get("department_card_ids", []))
        if snapshot.get("event_card_id"):
            ids.append(snapshot["event_card_id"])
        return all(card_visible(db.get(models.Card, cid), user) for cid in ids)

    def accessible_receipt(cached, user, db):
        # A replay can contain historical cards that have since become restricted.
        if not snapshot_visible(cached, user, db):
            raise HTTPException(404, "Таска не найдена или недоступна")
        return cached

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

    if bot_mode:
        allowed = {(prefix, 'GET'), (prefix, 'POST'), (prefix + '/options', 'GET'),
                   (prefix + '/{task_id}', 'GET'), (prefix + '/{task_id}', 'PATCH')}
        for suffix in ('/preferences/notifications', '/{task_id}/preferences/notifications'):
            allowed.update((prefix + suffix, method) for method in ('GET', 'PUT'))
        router.routes = [route for route in router.routes if any((route.path, method) in allowed for method in route.methods)]
    return router
