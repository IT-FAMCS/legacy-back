"""Shared budget ledger, atomic decisions and durable Telegram delivery."""
import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload, selectinload

import models
from database import get_db
from tasks import begin_task_write
from telegram_auth import bot_actor, service_access, no_store, replay, remember


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def money(value):
    return format(Decimal(value), '.2f')


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Amount(Input):
    amount: Decimal

    @field_validator('amount', mode='before')
    @classmethod
    def validate_amount(cls, value):
        if isinstance(value, bool):
            raise ValueError('Введите сумму в BYN')
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise ValueError('Введите сумму в BYN')
        if not amount.is_finite() or not 0 < amount <= Decimal('9999999999.99') or amount != amount.quantize(Decimal('.01')):
            raise ValueError('Сумма должна быть положительной, не больше 9999999999.99 BYN и содержать не более двух знаков после запятой')
        return amount


class EntryCreate(Amount):
    direction: Literal['income', 'expense']
    account: Literal['cash', 'card']
    description: str = Field(min_length=1, max_length=2000)
    operation_at: datetime


class EntryUpdate(Input):
    direction: Optional[Literal['income', 'expense']] = None
    account: Optional[Literal['cash', 'card']] = None
    amount: Optional[Decimal] = None
    description: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    operation_at: Optional[datetime] = None

    @model_validator(mode='after')
    def validate_fields(self):
        values = self.model_dump(exclude_unset=True)
        if not values or any(value is None for value in values.values()):
            raise ValueError('Передайте непустые поля операции')
        return self

    @field_validator('amount', mode='before')
    @classmethod
    def validate_amount(cls, value):
        return Amount.validate_amount(value)


class EntryDelete(Input):
    expected_updated_at: datetime


class RequestCreate(Amount):
    request_type: Literal['reimbursement', 'add_funds']
    description: str = Field(min_length=1, max_length=1500)
    payout_details: Optional[str] = Field(default=None, min_length=1, max_length=500)
    telegram_file_id: Optional[str] = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode='after')
    def required_details(self):
        if self.request_type == 'reimbursement' and (not self.payout_details or not self.telegram_file_id):
            raise ValueError('Для возврата нужны реквизиты и фото чека')
        if self.request_type == 'add_funds' and self.payout_details:
            raise ValueError('Для пополнения реквизиты не нужны')
        return self


class Decision(Input):
    decision: Literal['approved', 'rejected']
    account: Optional[Literal['cash', 'card']] = None
    expected_amount: Optional[Decimal] = None

    @model_validator(mode='after')
    def validate_decision(self):
        if self.decision == 'approved' and (self.account is None or self.expected_amount is None or not self.expected_amount.is_finite()):
            raise ValueError('Выберите счёт и подтвердите текущую сумму')
        if self.decision == 'rejected' and self.account is not None:
            raise ValueError('При отказе счёт не выбирается')
        return self


class Claim(Input):
    limit: int = Field(default=1, ge=1, le=20)


class Lease(Input):
    lease_token: str = Field(min_length=1, max_length=64)


class Ack(Input):
    lease_token: str = Field(min_length=1, max_length=64)
    outcome: Literal['delivered', 'retry', 'permanent', 'blocked', 'progress']
    detail_message_id: Optional[int] = Field(default=None, gt=0)
    photo_message_id: Optional[int] = Field(default=None, gt=0)
    outcome_message_id: Optional[int] = Field(default=None, gt=0)
    retry_after: int = Field(default=30, ge=1, le=86400)


def person_name(user):
    return ' '.join(filter(None, [user.last_name, user.first_name, user.middle_name])) if user else None


def request_data(row):
    return dict(id=row.id, request_type=row.request_type, applicant_name=row.applicant_name,
                applicant_id=row.applicant_id, applicant_login=row.applicant.login if row.applicant else None,
                departments=json.loads(row.department_snapshot), description=row.description,
                submitted_amount=money(row.submitted_amount), amount=money(row.amount),
                payout_details=row.payout_details, status=row.status, account=row.account,
                created_at=row.created_at, processed_at=row.processed_at, processed_by=row.processed_by,
                amount_changes=[dict(old_amount=money(c.old_amount), new_amount=money(c.new_amount),
                                     editor_id=c.editor_id, created_at=c.created_at) for c in row.amount_changes])


def entry_data(row):
    result = {key: money(getattr(row, key)) if key == 'amount' else getattr(row, key)
              for key in ('id', 'direction', 'account', 'amount', 'description', 'operation_at',
                          'request_id', 'created_by', 'updated_by', 'created_at', 'updated_at')}
    result.update(created_by_name=person_name(row.creator), created_by_login=row.creator.login if row.creator else None,
                  updated_by_name=person_name(row.editor), updated_by_login=row.editor.login if row.editor else None,
                  applicant_name=row.request.applicant_name if row.request else None,
                  applicant_login=row.request.applicant.login if row.request and row.request.applicant else None)
    return result


def active(binding):
    return binding and binding.active and not binding.blocked and binding.chat_id and binding.user.is_active and not binding.user.is_deactivated


def cashiers(db):
    return [b for b in db.query(models.TelegramBinding).filter_by(active=True, blocked=False).all()
            if active(b) and b.user.position_ref.is_kaznachey]


def queue(db, row, kind, bindings):
    for binding in bindings:
        db.add(models.BudgetNotification(event_type=kind, request_id=row.id, recipient_user_id=binding.user_id,
            binding_id=binding.id, generation=binding.generation))


def corrected(db, row, amount, editor):
    if row.status != 'pending':
        raise HTTPException(409, 'Заявка уже обработана')
    if row.amount != amount:
        db.add(models.BudgetRequestAmountChange(request_id=row.id, old_amount=row.amount, new_amount=amount, editor_id=editor.id))
        row.amount = amount
        queue(db, row, 'correction', cashiers(db))


def create_budget_router(*, get_current_user, is_executive):
    router = APIRouter(tags=['Budget'], dependencies=[Depends(no_store)])

    def manager(user=Depends(get_current_user)):
        if not (is_executive(user.position_ref) or user.position_ref.is_kaznachey):
            raise HTTPException(403, 'Бюджет доступен только руководству и казначеям')
        return user

    def find(db, model, rid):
        row = db.get(model, rid)
        if row is None:
            raise HTTPException(404, 'Запись не найдена')
        return row

    @router.get('/api/budget')
    def ledger(user=Depends(manager), db: Session=Depends(get_db)):
        balances = dict(cash=Decimal(0), card=Decimal(0))
        groups = {}
        for row in db.query(models.BudgetEntry).options(
            joinedload(models.BudgetEntry.creator), joinedload(models.BudgetEntry.editor),
            joinedload(models.BudgetEntry.request).joinedload(models.BudgetRequest.applicant)
        ).filter(models.BudgetEntry.deleted_at.is_(None)).order_by(models.BudgetEntry.operation_at.desc(), models.BudgetEntry.id.desc()).all():
            balances[row.account] += row.amount if row.direction == 'income' else -row.amount
            local = row.operation_at + timedelta(hours=3)
            groups.setdefault(local.year, {}).setdefault(local.month, []).append(entry_data(row))
        return dict(balances={k: money(v) for k, v in balances.items()}, years=[
            dict(year=year, months=[dict(month=month, entries=entries) for month, entries in months.items()])
            for year, months in groups.items()])

    @router.post('/api/budget/entries', status_code=201)
    def create_entry(data: EntryCreate, user=Depends(manager), db: Session=Depends(get_db)):
        values = data.model_dump()
        stamp = values['operation_at']
        values['operation_at'] = stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp.tzinfo else stamp
        row = models.BudgetEntry(**values, created_by=user.id, updated_by=user.id)
        db.add(row); db.commit(); db.refresh(row)
        return entry_data(row)

    @router.patch('/api/budget/entries/{rid}')
    def edit_entry(rid: int, data: EntryUpdate, user=Depends(manager), db: Session=Depends(get_db)):
        begin_task_write(db)
        row = find(db, models.BudgetEntry, rid)
        if row.deleted_at is not None: raise HTTPException(404, 'Операция удалена')
        for key, value in data.model_dump(exclude_unset=True).items():
            if key == 'operation_at' and value.tzinfo:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            setattr(row, key, value)
        row.updated_by = user.id
        db.commit(); db.refresh(row)
        return entry_data(row)

    @router.delete('/api/budget/entries/{rid}')
    def delete_entry(rid: int, data: EntryDelete, user=Depends(manager), db: Session=Depends(get_db)):
        begin_task_write(db)
        row = find(db, models.BudgetEntry, rid)
        if row.deleted_at is None:
            expected = data.expected_updated_at
            if expected.tzinfo: expected = expected.astimezone(timezone.utc).replace(tzinfo=None)
            if row.updated_at != expected:
                raise HTTPException(409, 'Операция была изменена. Обновите данные и подтвердите удаление заново.')
            row.deleted_at, row.deleted_by = now(), user.id
        db.commit()
        return {'ok': True}

    @router.get('/api/budget/requests')
    def requests(status: Literal['pending']='pending', user=Depends(manager), db: Session=Depends(get_db)):
        return [request_data(row) for row in db.query(models.BudgetRequest).options(joinedload(models.BudgetRequest.applicant), selectinload(models.BudgetRequest.amount_changes)).filter_by(status=status).order_by(models.BudgetRequest.created_at).all()]

    @router.patch('/api/budget/requests/{rid}')
    def correct(rid: int, data: Amount, user=Depends(manager), db: Session=Depends(get_db)):
        begin_task_write(db)
        row = find(db, models.BudgetRequest, rid)
        corrected(db, row, data.amount, user)
        db.commit(); db.refresh(row)
        return request_data(row)

    @router.post('/api/telegram/bot/budget/requests', status_code=201)
    def submit(data: RequestCreate, user=Depends(bot_actor), db: Session=Depends(get_db)):
        saved = replay(db, user)
        if saved is not None: return saved
        recipients = cashiers(db)
        if not recipients:
            raise HTTPException(409, 'Заявку пока не удалось отправить. Черновик сохранён; попробуйте позже.')
        row = models.BudgetRequest(**data.model_dump(), submitted_amount=data.amount, applicant_id=user.id,
            applicant_name=' '.join(filter(None, [user.last_name, user.first_name, user.middle_name])),
            department_snapshot=json.dumps([link.department.name for link in user.departments], ensure_ascii=False))
        db.add(row); db.flush()
        queue(db, row, 'request', recipients)
        result = request_data(row)
        remember(db, user, result); db.commit()
        return result

    @router.get('/api/telegram/bot/budget/requests/{rid}')
    def bot_request(rid: int, user=Depends(bot_actor), db: Session=Depends(get_db)):
        row = find(db, models.BudgetRequest, rid)
        if not user.position_ref.is_kaznachey:
            raise HTTPException(403, 'Действие доступно казначею')
        return request_data(row)

    @router.patch('/api/telegram/bot/budget/requests/{rid}')
    def bot_correct(rid: int, data: Amount, user=Depends(bot_actor), db: Session=Depends(get_db)):
        if not user.position_ref.is_kaznachey: raise HTTPException(403, 'Действие доступно казначею')
        saved = replay(db, user)
        if saved is not None: return saved
        row = find(db, models.BudgetRequest, rid)
        corrected(db, row, data.amount, user)
        db.flush(); db.refresh(row)
        result = request_data(row)
        remember(db, user, result); db.commit()
        return result

    @router.post('/api/telegram/bot/budget/requests/{rid}/decision')
    def decide(rid: int, data: Decision, user=Depends(bot_actor), db: Session=Depends(get_db)):
        # bot_actor acquires BEGIN IMMEDIATE before reading binding/permissions.
        if not user.position_ref.is_kaznachey: raise HTTPException(403, 'Действие доступно казначею')
        saved = replay(db, user)
        if saved is not None: return saved
        row = find(db, models.BudgetRequest, rid)
        if row.status != 'pending': raise HTTPException(409, 'Заявка уже обработана')
        if data.decision == 'approved' and row.amount != data.expected_amount:
            raise HTTPException(409, 'Сумма изменилась. Откройте заявку заново и подтвердите текущую сумму.')
        if data.decision == 'approved' and db.query(models.BudgetNotification.id).filter(
            models.BudgetNotification.request_id == rid,
            models.BudgetNotification.event_type.in_(['request', 'correction']),
            models.BudgetNotification.state.in_(['pending', 'leased'])).first():
            raise HTTPException(409, 'Дождитесь доставки актуальной суммы всем казначеям и повторите одобрение.')
        # Supersede delayed retries; leased sends finish/expire before cleanup can run.
        db.query(models.BudgetNotification).filter(
            models.BudgetNotification.request_id == rid,
            models.BudgetNotification.event_type.in_(['request', 'correction']),
            models.BudgetNotification.state == 'pending').update({'state':'cancelled'}, synchronize_session=False)
        row.status = row.decision = data.decision
        row.account, row.processed_by, row.processed_at = data.account, user.id, now()
        if data.decision == 'approved':
            db.add(models.BudgetEntry(direction='expense' if row.request_type == 'reimbursement' else 'income',
                account=data.account, amount=row.amount, description=row.description, operation_at=now(),
                created_by=user.id, updated_by=user.id, request_id=row.id))
        # Include prior recipients even if cashier permission changed: old buttons must disappear.
        recipient_ids = {n.binding_id for n in db.query(models.BudgetNotification).filter_by(request_id=rid).all()}
        queue(db, row, 'processed', [b for bid in recipient_ids if (b := db.get(models.TelegramBinding, bid)) is not None])
        applicant = db.query(models.TelegramBinding).filter_by(user_id=row.applicant_id, active=True).first()
        if active(applicant): queue(db, row, 'outcome', [applicant])
        result = request_data(row)
        remember(db, user, result); db.commit()
        return result

    @router.post('/api/telegram/budget/notifications/claim', dependencies=[Depends(service_access)])
    def claim(data: Claim, db: Session=Depends(get_db)):
        begin_task_write(db)
        clock = now(); result = []
        candidates = db.query(models.BudgetNotification).filter(
            or_(models.BudgetNotification.state == 'pending',
                (models.BudgetNotification.state == 'leased') & (models.BudgetNotification.lease_until < clock)),
            models.BudgetNotification.next_attempt_at <= clock).order_by(models.BudgetNotification.id).all()
        for event in candidates:
            if len(result) >= data.limit: break
            earlier = db.query(models.BudgetNotification.id).filter(
                models.BudgetNotification.request_id == event.request_id,
                models.BudgetNotification.binding_id == event.binding_id,
                models.BudgetNotification.id < event.id,
                models.BudgetNotification.state.in_(['pending', 'leased'])).first()
            if earlier: continue
            binding = db.get(models.TelegramBinding, event.binding_id)
            if not active(binding) or binding.generation != event.generation:
                event.state = 'cancelled'; db.flush(); continue
            row = db.get(models.BudgetRequest, event.request_id)
            if event.event_type in ('request', 'correction') and (row.status != 'pending' or not binding.user.position_ref.is_kaznachey):
                event.state = 'cancelled'; db.flush(); continue
            delivery = db.query(models.BudgetNotificationDelivery).filter_by(request_id=row.id, binding_id=binding.id, generation=binding.generation).first()
            event.state, event.lease_token, event.lease_until = 'leased', secrets.token_hex(24), clock + timedelta(minutes=2)
            event.attempts += 1
            result.append(dict(id=event.id, event_type=event.event_type, request=request_data(row),
                telegram_file_id=row.telegram_file_id if event.event_type in ('request', 'correction') else None,
                telegram_user_id=binding.telegram_user_id, chat_id=binding.chat_id, generation=binding.generation,
                lease_token=event.lease_token, detail_message_id=delivery.detail_message_id if delivery else None,
                photo_message_id=delivery.photo_message_id if delivery else None,
                outcome_message_id=json.loads(event.payload or '{}').get('outcome_message_id')))
        db.commit()
        return result

    @router.post('/api/telegram/budget/notifications/{eid}/validate', dependencies=[Depends(service_access)])
    def validate(eid: int, data: Lease, db: Session=Depends(get_db)):
        begin_task_write(db)
        event = find(db, models.BudgetNotification, eid)
        if event.state != 'leased' or event.lease_token != data.lease_token or event.lease_until < now():
            return {'valid':False}
        binding = db.get(models.TelegramBinding, event.binding_id)
        if not active(binding) or binding.generation != event.generation:
            return {'valid':False}
        row = db.get(models.BudgetRequest, event.request_id)
        if event.event_type in ('request','correction') and (not binding.user.position_ref.is_kaznachey or row.status != 'pending'):
            return {'valid':False}
        cleanup_only = event.event_type == 'processed' and not binding.user.position_ref.is_kaznachey
        return dict(valid=True, cleanup_only=cleanup_only,
            request=None if cleanup_only else request_data(row),
            telegram_file_id=row.telegram_file_id if event.event_type in ('request','correction') else None)

    @router.post('/api/telegram/budget/notifications/{eid}/ack', dependencies=[Depends(service_access)])
    def ack(eid: int, data: Ack, db: Session=Depends(get_db)):
        begin_task_write(db)
        event = find(db, models.BudgetNotification, eid)
        if event.state != 'leased' or event.lease_token != data.lease_token or event.lease_until < now():
            raise HTTPException(409, 'Срок доставки истёк')
        if data.detail_message_id or data.photo_message_id:
            delivery = db.query(models.BudgetNotificationDelivery).filter_by(request_id=event.request_id, binding_id=event.binding_id, generation=event.generation).first()
            if not delivery:
                delivery = models.BudgetNotificationDelivery(request_id=event.request_id, binding_id=event.binding_id, generation=event.generation)
                db.add(delivery)
            if data.detail_message_id: delivery.detail_message_id = data.detail_message_id
            if data.photo_message_id: delivery.photo_message_id = data.photo_message_id
        if data.outcome_message_id:
            payload = json.loads(event.payload or '{}')
            payload['outcome_message_id'] = data.outcome_message_id
            event.payload = json.dumps(payload)
        if data.outcome == 'progress':
            db.commit()
            return {'ok': True}
        if data.outcome == 'blocked':
            binding = db.get(models.TelegramBinding, event.binding_id)
            if binding and binding.generation == event.generation:
                binding.blocked = True
        request = db.get(models.BudgetRequest, event.request_id)
        superseded = event.event_type in ('request','correction') and request.status != 'pending'
        event.state = 'pending' if data.outcome == 'retry' and not superseded else 'delivered' if data.outcome == 'delivered' else 'cancelled'
        event.next_attempt_at = now() + timedelta(seconds=data.retry_after)
        event.lease_token = None; event.lease_until = None
        db.commit()
        return {'ok': True}

    return router
