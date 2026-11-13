"""Service authentication and atomic domain request receipts; no Telegram token."""
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session
import models
from database import get_db


def no_store(response: Response):
    response.headers['Cache-Control'] = 'no-store'


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def configured():
    import re
    return len(os.getenv('TELEGRAM_SERVICE_KEY', '')) >= 32 and bool(re.fullmatch(r'[A-Za-z0-9_]{5,32}', os.getenv('TELEGRAM_BOT_USERNAME', '')))


def rate_limit(db, key, limit=10):
    """Called inside a serialized write transaction; durable across workers."""
    current = now()
    row = db.get(models.TelegramRateLimit, key)
    if row is None:
        row = models.TelegramRateLimit(key=key, window=current, count=0)
        db.add(row)
    if row.window < current - timedelta(minutes=1):
        row.window, row.count = current, 0
    row.count += 1
    if row.count > limit:
        db.commit()
        raise HTTPException(429, 'Слишком много попыток. Повторите через минуту.', headers={'Retry-After':'60'})


def service_access(request: Request, key: str = Header(default='', alias='X-Telegram-Service-Key'), db: Session = Depends(get_db)):
    expected = os.getenv('TELEGRAM_SERVICE_KEY', '')
    if len(expected) < 32:
        raise HTTPException(503, 'Интеграция Telegram пока не настроена')
    if not hmac.compare_digest(key.encode(), expected.encode()):
        from tasks import begin_task_write
        begin_task_write(db)
        rate_limit(db, 'service:' + (request.client.host if request.client else 'unknown'), 30)
        db.commit()
        raise HTTPException(401, 'Неверная авторизация сервиса')
    return True


async def bot_actor(request: Request, _service=Depends(service_access),
                    tid: int = Header(gt=0, alias='X-Telegram-User-ID'),
                    request_id: str = Header(default='', alias='X-Telegram-Request-ID'),
                    generation: str = Header(default='', alias='X-Telegram-Binding'),
                    db: Session = Depends(get_db)):
    writing = request.method not in {'GET', 'HEAD'}
    if writing:
        if not request_id or len(request_id) > 120:
            raise HTTPException(422, 'Нужен идентификатор операции')
        from tasks import begin_task_write
        begin_task_write(db)
    binding = db.query(models.TelegramBinding).filter_by(telegram_user_id=tid, active=True).first()
    if binding is None or not binding.user.is_active or binding.user.is_deactivated:
        raise HTTPException(403, 'Привяжите действующий аккаунт Наследия в своём профиле')
    if (writing and not generation) or (generation and not hmac.compare_digest(generation,binding.generation)):
        raise HTTPException(403, 'Привязка Telegram изменилась. Откройте меню заново.')
    user = binding.user
    user._telegram_binding = binding
    if writing:
        key = hashlib.sha256(f'{binding.id}:{binding.generation}:{request_id}'.encode()).hexdigest()
        fingerprint = hashlib.sha256(request.method.encode() + request.url.path.encode() + await request.body()).hexdigest()
        user._telegram_operation = (key, fingerprint)
    else:
        user._telegram_operation = None
    return user


def replay(db, user):
    operation = getattr(user, '_telegram_operation', None)
    if operation is None:
        return None
    key, fingerprint = operation
    row = db.get(models.TelegramRequestReceipt, key)
    if row:
        if row.fingerprint != fingerprint:
            raise HTTPException(409, 'Этот идентификатор уже использован для другой операции')
        return json.loads(row.response_json)
    return None


def remember(db, user, response):
    operation = getattr(user, '_telegram_operation', None)
    if operation is not None:
        key, fingerprint = operation
        db.add(models.TelegramRequestReceipt(key=key, fingerprint=fingerprint,
            response_json=json.dumps(jsonable_encoder(response), ensure_ascii=False), created_at=now()))
