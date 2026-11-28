"""Atomic task event outbox, calendar reminders, leased delivery and retry."""
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
import models
from database import get_db
from telegram_auth import service_access, no_store


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def muted(db, user_id, task_id):
    return db.query(models.TaskNotificationMute.user_id).filter(
        models.TaskNotificationMute.user_id == user_id,
        models.TaskNotificationMute.scope.in_([0, task_id]),
    ).first() is not None


def enqueue_assignment_changes(db, task, before, after, actor, check_card, record, bindings=None):
    """Store one event per recipient in the task transaction."""
    previous = set(before['assignee_ids']) if before else set()
    current = set(after['assignee_ids'])
    added, removed = current - previous, previous - current
    # A membership change supersedes pending alerts even when the actor is the
    # recipient or their binding/settings prevent a replacement notification.
    for users, kinds in [(removed, ['assigned']), (removed - {task.creator_id}, ['updated', 'deadline']),
                         (added, ['removed'])]:
        if users:
            db.query(models.TelegramNotification).filter(
                models.TelegramNotification.task_id == task.id,
                models.TelegramNotification.user_id.in_(users),
                models.TelegramNotification.kind.in_(kinds),
                models.TelegramNotification.state.in_(['pending', 'leased']),
            ).update({'state': 'cancelled'}, synchronize_session=False)
    recipients = current | removed | {task.creator_id}
    recipients.discard(actor.id)
    if not recipients:
        return
    if bindings is None:
        bindings = db.query(models.TelegramBinding).filter(
            models.TelegramBinding.active.is_(True), models.TelegramBinding.user_id.in_(recipients),
        ).all()
    else:
        bindings = [binding for binding in bindings if binding.user_id in recipients]
    clock = now()
    for binding in bindings:
        uid = binding.user_id
        user = binding.user
        if not user.is_active or user.is_deactivated or muted(db, uid, task.id):
            continue
        kind = 'assigned' if uid in added else 'removed' if uid in removed else 'updated'
        if kind != 'removed' and not visible(task, user, check_card):
            continue
        if record.id is None:
            db.flush()
        db.add(models.TelegramNotification(event_key=f'history:{record.id}:{kind}:{uid}',
            task_id=task.id, user_id=uid, binding_id=binding.id, generation=binding.generation,
            kind=kind, state='pending', created_at=clock, next_attempt_at=clock, attempts=0))


def snapshot_visible(db, snapshot, user, check_card):
    ids = list(snapshot.get('department_card_ids', []))
    if snapshot.get('event_card_id'):
        ids.append(snapshot['event_card_id'])
    for cid in ids:
        card = db.get(models.Card, cid)
        if card is None or not check_card(user, card):
            return False
    return True


def short_value(value, limit=100):
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[:limit - 1] + '…'


def field_value(snapshot, field):
    if field in ('assignee_ids', 'department_card_ids'):
        items, label = ('assignees', 'name') if field == 'assignee_ids' else ('departments', 'title')
        value = ', '.join(row[label] for row in snapshot[items])
    elif field == 'event_card_id':
        value = (snapshot.get('event') or {}).get('title')
    elif field == 'completed':
        value = 'выполнена' if snapshot[field] else 'в работе'
    elif field == 'archived':
        value = 'в архиве' if snapshot[field] else 'активна'
    else:
        value = snapshot.get(field)
    return short_value(value or '—')


def removed_message(db, entry, user, check_card):
    if entry.event_key.startswith('history:'):
        record = db.get(models.TaskHistory, int(entry.event_key.split(':')[1]))
        if record and record.task_id == entry.task_id and record.before_json:
            before = json.loads(record.before_json)
            if snapshot_visible(db, before, user, check_card):
                return f'Вас сняли с ответственности за таску «{short_value(before["title"])}».'
    return 'Вас сняли с ответственности за таску.'


def update_message(db, entry, task, user, check_card):
    record = db.get(models.TaskHistory, int(entry.event_key.split(':')[1]))
    if record is None or record.task_id != task.id:
        return None
    before = json.loads(record.before_json) if record.before_json else {}
    after = json.loads(record.after_json)
    if not all(snapshot_visible(db, snapshot, user, check_card) for snapshot in (before, after)):
        return f'Изменена таска «{short_value(task.title)}».'
    lines = [f'{short_value(record.actor_name)} изменил(а) таску «{short_value(after["title"])}».']
    fields = [('title', 'Название'), ('description', 'Описание'), ('deadline', 'Дедлайн'),
              ('assignee_ids', 'Ответственные'), ('event_card_id', 'Мероприятие'),
              ('department_card_ids', 'Отделы'), ('completed', 'Статус'), ('archived', 'Архив')]
    for field, label in fields:
        if before.get(field) != after.get(field):
            lines.append(f'{label}: {field_value(before, field)} → {field_value(after, field)}')
    return '\n'.join(lines)


def visible(task, user, check_card):
    return (user.is_active and not user.is_deactivated
            and (task.event_card_id is None or (task.event_card is not None and check_card(user, task.event_card)))
            and all(link.card is not None and check_card(user, link.card) for link in task.department_links))


def reminder_time(clock):
    name = os.getenv('TELEGRAM_NOTIFICATION_TIMEZONE', 'Europe/Moscow')
    try:
        zone = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        # Windows Python may lack an IANA database. Current Moscow calendar uses UTC+3.
        if name != 'Europe/Moscow':
            raise HTTPException(503, 'На сервере нет базы выбранного часового пояса')
        zone = timezone(timedelta(hours=3))
    try:
        hour = int(os.getenv('TELEGRAM_REMINDER_HOUR', '10'))
    except ValueError:
        raise HTTPException(503, 'Неверно настроен час напоминаний')
    if not 0 <= hour <= 23:
        raise HTTPException(503, 'Неверно настроен час напоминаний')
    return clock.replace(tzinfo=timezone.utc).astimezone(zone), hour


def schedule_reminders(db, clock, check_card):
    local, hour = reminder_time(clock)
    if local.hour < hour:
        return
    deadline = (local.date() + timedelta(days=1)).isoformat()
    tasks = db.query(models.Task).filter_by(deadline=deadline, completed=False, archived=False).all()
    for task in tasks:
        recipients = {task.creator_id} | {link.user_id for link in task.assignee_links}
        for uid in sorted(recipients):
            binding = db.query(models.TelegramBinding).filter_by(user_id=uid, active=True, blocked=False).first()
            if not binding or muted(db, uid, task.id) or not visible(task, binding.user, check_card):
                continue
            key = f'deadline:{task.id}:{uid}:{deadline}'
            if db.query(models.TelegramNotification.id).filter_by(event_key=key).first():
                continue
            db.add(models.TelegramNotification(event_key=key, task_id=task.id, user_id=uid,
                binding_id=binding.id, generation=binding.generation, kind='deadline', deadline=deadline,
                state='pending', created_at=clock, next_attempt_at=clock, attempts=0))
    db.flush()


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Batch(Input):
    limit: int = Field(default=20, ge=1, le=50)


class Lease(Input):
    lease_token: str = Field(min_length=16, max_length=64)


class Ack(Lease):
    outcome: Literal['delivered', 'retry', 'blocked', 'permanent']
    retry_after: Optional[int] = Field(default=None, ge=1, le=86400)


def create_notification_router(*, has_access_to_card):
    router = APIRouter(prefix='/api/telegram/service/notifications', tags=['Telegram notifications'],
                       dependencies=[Depends(service_access),Depends(no_store)])

    def eligible_recipient(db, entry, binding, task):
        return (binding is not None and binding.active and binding.generation == entry.generation
                and binding.user_id == entry.user_id and binding.user.is_active and not binding.user.is_deactivated
                and task is not None and not muted(db, entry.user_id, entry.task_id))

    def delivery_text(db, entry, binding, task, clock):
        user = binding.user
        assigned = any(link.user_id == user.id for link in task.assignee_links)
        participant = assigned or task.creator_id == user.id
        can_view = visible(task, user, has_access_to_card)
        title = task.title[:300]
        if entry.kind == 'deadline':
            local, hour = reminder_time(clock)
            tomorrow = (local.date() + timedelta(days=1)).isoformat()
            if (task.completed or task.archived or not participant or not can_view
                    or task.deadline != entry.deadline or task.deadline != tomorrow or local.hour < hour):
                return None
            return f'Завтра дедлайн таски «{title}»: {task.deadline}.'

        if entry.created_at < clock - timedelta(days=1):
            return None
        if entry.kind == 'updated':
            if participant and can_view:
                return update_message(db, entry, task, user, has_access_to_card)
            return None

        newer = db.query(models.TelegramNotification.id).filter(
            models.TelegramNotification.task_id == entry.task_id,
            models.TelegramNotification.user_id == entry.user_id,
            models.TelegramNotification.kind.in_(['assigned', 'removed']),
            models.TelegramNotification.id > entry.id).first()
        if newer:
            return None
        if entry.kind == 'removed' and not assigned:
            if can_view:
                return removed_message(db, entry, user, has_access_to_card)
            return 'Вас сняли с ответственности за таску.'
        if entry.kind == 'assigned' and assigned and can_view and not task.archived:
            from tasks import display_name
            text = f'Вам назначена таска «{title}».\nСоздатель: {short_value(display_name(task.creator))}.'
            if task.completed:
                text += '\nСтатус: выполнена.'
            if task.deadline:
                text += f'\nДедлайн: {task.deadline}.'
            return text
        return None

    @router.post('/claim')
    def claim(data: Batch, db: Session = Depends(get_db)):
        from tasks import begin_task_write
        begin_task_write(db)
        clock = now()
        schedule_reminders(db, clock, has_access_to_card)
        rows = db.query(models.TelegramNotification).filter(or_(
            and_(models.TelegramNotification.state=='pending', models.TelegramNotification.next_attempt_at <= clock),
            and_(models.TelegramNotification.state=='leased', models.TelegramNotification.lease_until <= clock),
        )).order_by(models.TelegramNotification.id).limit(data.limit*5).all()
        result=[]
        for entry in rows:
            if len(result)>=data.limit:
                break
            binding=db.get(models.TelegramBinding,entry.binding_id)
            task=db.get(models.Task,entry.task_id)
            if not eligible_recipient(db, entry, binding, task):
                entry.state = 'cancelled'
                continue
            if binding.blocked:
                entry.state='pending'
                entry.next_attempt_at=clock+timedelta(hours=1)
                continue
            if entry.attempts >= 8:
                entry.state='dead'
                continue
            text = delivery_text(db, entry, binding, task, clock)
            if text is None:
                entry.state = 'cancelled'
                continue
            entry.state='leased'
            entry.lease_token=secrets.token_hex(16)
            entry.lease_until=clock+timedelta(seconds=90)
            entry.attempts+=1
            result.append({'id':entry.id,'lease_token':entry.lease_token,'telegram_user_id':binding.telegram_user_id,
                           'chat_id':binding.chat_id,'generation':entry.generation,'task_id':task.id,
                           'kind':entry.kind,'text':text})
        db.commit()
        return result

    @router.post('/{notification_id}/validate')
    def validate(notification_id: int, data: Lease, db: Session = Depends(get_db)):
        from tasks import begin_task_write
        begin_task_write(db)
        entry = db.get(models.TelegramNotification, notification_id)
        clock = now()
        if (entry is None or entry.state != 'leased' or not entry.lease_token
                or not hmac.compare_digest(entry.lease_token.encode(), data.lease_token.encode())
                or not entry.lease_until or entry.lease_until <= clock):
            return {'valid': False}
        binding = db.get(models.TelegramBinding, entry.binding_id)
        task = db.get(models.Task, entry.task_id)
        if not eligible_recipient(db, entry, binding, task) or binding.blocked:
            return {'valid': False}
        text = delivery_text(db, entry, binding, task, clock)
        return {'valid': True, 'text': text} if text is not None else {'valid': False}

    @router.post('/{notification_id}/ack')
    def ack(notification_id: int, data: Ack, db: Session = Depends(get_db)):
        from tasks import begin_task_write
        begin_task_write(db)
        entry=db.get(models.TelegramNotification,notification_id)
        if entry is None:
            raise HTTPException(404,'Уведомление не найдено')
        if not entry.lease_token or not hmac.compare_digest(entry.lease_token,data.lease_token):
            raise HTTPException(409,'Срок доставки изменился')
        if entry.state!='leased':
            return {'id':entry.id,'state':entry.state}
        if data.outcome=='delivered':
            entry.state='delivered'
        elif data.outcome in {'blocked','permanent'}:
            entry.state='dead'
            entry.last_error=data.outcome
            if data.outcome=='blocked':
                binding=db.get(models.TelegramBinding,entry.binding_id)
                if binding and binding.generation==entry.generation:
                    binding.blocked=True
        elif entry.attempts>=8:
            entry.state='dead'
            entry.last_error='retry_exhausted'
        else:
            entry.state='pending'
            entry.last_error='temporary'
            delay=max(min(3600,5*2**entry.attempts),data.retry_after or 0)
            entry.next_attempt_at=now()+timedelta(seconds=delay)
        db.commit()
        return {'id':entry.id,'state':entry.state}

    return router
