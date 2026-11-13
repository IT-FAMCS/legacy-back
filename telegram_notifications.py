"""Atomic task event outbox, calendar reminders, leased delivery and retry."""
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
import models
from database import get_db
from telegram_auth import service_access, no_store


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue_assignment_changes(db, task, before):
    """Never commit here: task mutation, audit and notifications are one transaction."""
    previous = set(before['assignee_ids']) if before else set()
    current = {link.user_id for link in task.assignee_links}
    for kind, recipients in [('assigned', current-previous), ('removed', previous-current)]:
        for uid in recipients:
            binding = db.query(models.TelegramBinding).filter_by(user_id=uid, active=True).first()
            if not binding or not binding.user.is_active or binding.user.is_deactivated:
                continue
            db.add(models.TelegramNotification(event_key=f'{kind}:{task.id}:{task.version}:{uid}',
                task_id=task.id, user_id=uid, binding_id=binding.id, generation=binding.generation,
                kind=kind, state='pending', created_at=now(), next_attempt_at=now(), attempts=0))


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
        for link in task.assignee_links:
            binding = db.query(models.TelegramBinding).filter_by(user_id=link.user_id, active=True, blocked=False).first()
            if not binding or not visible(task, binding.user, check_card):
                continue
            key = f'deadline:{task.id}:{link.user_id}:{deadline}'
            if db.query(models.TelegramNotification.id).filter_by(event_key=key).first():
                continue
            db.add(models.TelegramNotification(event_key=key, task_id=task.id, user_id=link.user_id,
                binding_id=binding.id, generation=binding.generation, kind='deadline', deadline=deadline,
                state='pending', created_at=clock, next_attempt_at=clock, attempts=0))
    db.flush()


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Batch(Input):
    limit: int = Field(default=20, ge=1, le=50)


class Ack(Input):
    lease_token: str = Field(min_length=16, max_length=64)
    outcome: Literal['delivered', 'retry', 'blocked', 'permanent']
    retry_after: Optional[int] = Field(default=None, ge=1, le=86400)


def create_notification_router(*, has_access_to_card):
    router = APIRouter(prefix='/api/telegram/service/notifications', tags=['Telegram notifications'],
                       dependencies=[Depends(service_access),Depends(no_store)])

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
            if (not binding or not binding.active or binding.generation!=entry.generation
                    or not binding.user.is_active or binding.user.is_deactivated or task is None):
                entry.state='cancelled'
                continue
            if binding.blocked:
                entry.state='pending'
                entry.next_attempt_at=clock+timedelta(hours=1)
                continue
            if entry.attempts >= 8:
                entry.state='dead'
                continue
            assigned=any(link.user_id==entry.user_id for link in task.assignee_links)
            can_view=visible(task,binding.user,has_access_to_card)
            if entry.kind=='deadline':
                local,hour=reminder_time(clock)
                valid=(not task.completed and not task.archived and assigned and can_view
                       and task.deadline==entry.deadline
                       and task.deadline==(local.date()+timedelta(days=1)).isoformat() and local.hour>=hour)
            else:
                newer=db.query(models.TelegramNotification.id).filter(
                    models.TelegramNotification.task_id==entry.task_id,
                    models.TelegramNotification.user_id==entry.user_id,
                    models.TelegramNotification.kind.in_(['assigned','removed']),
                    models.TelegramNotification.id>entry.id).first()
                valid=(newer is None and entry.created_at>=clock-timedelta(days=1)
                       and ((entry.kind=='assigned' and assigned and can_view and not task.completed and not task.archived)
                            or (entry.kind=='removed' and not assigned)))
            if not valid:
                entry.state='cancelled'
                continue
            title=task.title[:300]
            if entry.kind=='removed':
                text=f'Вас сняли с ответственности за таску «{title}».' if can_view else 'Вас сняли с ответственности за таску. Подробности недоступны по текущим правам.'
            elif entry.kind=='assigned':
                text=f'Вам назначена таска «{title}».'
                if task.deadline:
                    text+=f'\nДедлайн: {task.deadline}.'
            else:
                text=f'Завтра дедлайн таски «{title}»: {task.deadline}.'
            entry.state='leased'
            entry.lease_token=secrets.token_hex(16)
            entry.lease_until=clock+timedelta(seconds=90)
            entry.attempts+=1
            result.append({'id':entry.id,'lease_token':entry.lease_token,'telegram_user_id':binding.telegram_user_id,
                           'chat_id':binding.chat_id,'generation':entry.generation,'task_id':task.id,
                           'kind':entry.kind,'text':text})
        db.commit()
        return result

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
