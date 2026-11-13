"""Two-phase account linking. The editable Telegram contact is never identity."""
import hashlib
import os
import secrets
from datetime import timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
import models
from database import get_db
from telegram_auth import bot_actor, configured, now, rate_limit, service_access


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Claim(Input):
    token: str = Field(min_length=20, max_length=80)
    telegram_user_id: int = Field(gt=0)
    chat_id: int = Field(gt=0)
    username: Optional[str] = Field(default=None, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=200)


class Confirmation(Input):
    challenge_id: int = Field(gt=0)


def no_cache(response: Response):
    response.headers['Cache-Control'] = 'no-store'


def revoke(db, binding):
    if binding:
        db.query(models.TelegramNotification).filter_by(binding_id=binding.id, generation=binding.generation).filter(
            models.TelegramNotification.state.in_(['pending', 'leased'])).update({'state':'cancelled'}, synchronize_session=False)
        binding.active = False
        binding.telegram_user_id = None
        binding.chat_id = None
        binding.generation = secrets.token_hex(16)
        binding.blocked = False


def binding_data(binding):
    return {'telegram_user_id':binding.telegram_user_id, 'username':binding.username,
            'display_name':binding.display_name, 'linked_at':binding.linked_at.isoformat() + 'Z'}


def create_telegram_router(*, get_current_user):
    router = APIRouter(prefix='/api/telegram', tags=['Telegram'], dependencies=[Depends(no_cache)])

    def lock(db):
        from tasks import begin_task_write
        begin_task_write(db)

    @router.get('/link')
    def status(user=Depends(get_current_user), db: Session = Depends(get_db)):
        binding = db.query(models.TelegramBinding).filter_by(user_id=user.id, active=True).first()
        pending = db.query(models.TelegramLinkChallenge).filter_by(user_id=user.id, consumed_at=None).filter(
            models.TelegramLinkChallenge.expires_at > now()).order_by(models.TelegramLinkChallenge.id.desc()).first()
        return {'enabled':configured(), 'binding':binding_data(binding) if binding else None,
                'pending': {'id':pending.id, 'expires_at':pending.expires_at.isoformat()+'Z',
                            'telegram_user_id':pending.telegram_user_id, 'username':pending.username,
                            'display_name':pending.display_name} if pending else None}

    @router.post('/link', status_code=201)
    def issue(user=Depends(get_current_user), db: Session = Depends(get_db)):
        if not configured():
            raise HTTPException(503, 'Интеграция Telegram пока не настроена')
        lock(db)
        rate_limit(db, f'issue:{user.id}')
        if db.query(models.TelegramBinding).filter_by(user_id=user.id, active=True).first():
            raise HTTPException(409, 'Сначала отключите текущую привязку')
        db.query(models.TelegramLinkChallenge).filter_by(user_id=user.id, consumed_at=None).update({'consumed_at':now()})
        token = secrets.token_urlsafe(32)
        challenge = models.TelegramLinkChallenge(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                                                 expires_at=now()+timedelta(minutes=10))
        db.add(challenge)
        db.commit()
        return {'id':challenge.id, 'token':token, 'expires_at':challenge.expires_at.isoformat()+'Z',
                'url':f"https://t.me/{os.environ['TELEGRAM_BOT_USERNAME']}?start=link_{token}"}

    @router.post('/bot/claim', dependencies=[Depends(service_access)])
    def claim(data: Claim, tid: int = Header(gt=0, alias='X-Telegram-User-ID'), db: Session = Depends(get_db)):
        if data.telegram_user_id != tid or data.chat_id != tid:
            raise HTTPException(422, 'Привязка возможна только в личном чате владельца Telegram')
        lock(db)
        rate_limit(db, f'claim:{tid}')
        challenge = db.query(models.TelegramLinkChallenge).filter_by(token_hash=hashlib.sha256(data.token.encode()).hexdigest()).first()
        if challenge is None or challenge.consumed_at or challenge.expires_at <= now():
            db.commit()
            raise HTTPException(410, 'Ссылка недействительна или истекла. Создайте новую в профиле.')
        bound = db.query(models.TelegramBinding).filter_by(telegram_user_id=tid, active=True).first()
        if bound is not None:
            db.commit()
            raise HTTPException(409, 'Этот Telegram уже привязан к аккаунту')
        if challenge.telegram_user_id is not None and challenge.telegram_user_id != tid:
            db.commit()
            raise HTTPException(409, 'Ссылка уже открыта другим Telegram. Отмените её в профиле.')
        target = db.get(models.User, challenge.user_id)
        if not target.is_active or target.is_deactivated:
            db.commit()
            raise HTTPException(403, 'Аккаунт отключён')
        challenge.telegram_user_id, challenge.chat_id = tid, data.chat_id
        challenge.username, challenge.display_name = data.username, data.display_name
        db.commit()
        return {'pending':True, 'message':'Вернитесь в профиль Наследия и подтвердите привязку Telegram.'}

    @router.post('/link/confirm')
    def confirm(data: Confirmation, user=Depends(get_current_user), db: Session = Depends(get_db)):
        lock(db)
        challenge = db.get(models.TelegramLinkChallenge, data.challenge_id)
        if challenge is None or challenge.user_id != user.id:
            raise HTTPException(404, 'Запрос привязки не найден')
        if challenge.consumed_at or challenge.expires_at <= now():
            raise HTTPException(410, 'Запрос привязки истёк или отменён')
        if challenge.telegram_user_id is None:
            raise HTTPException(409, 'Сначала откройте ссылку в боте')
        if db.query(models.TelegramBinding).filter_by(telegram_user_id=challenge.telegram_user_id, active=True).first():
            raise HTTPException(409, 'Telegram уже привязан. Повторите привязку после отключения.')
        binding = db.query(models.TelegramBinding).filter_by(user_id=user.id).first()
        if binding and binding.active:
            raise HTTPException(409, 'Аккаунт уже привязан')
        if binding is None:
            binding = models.TelegramBinding(user_id=user.id)
            db.add(binding)
        binding.telegram_user_id, binding.chat_id = challenge.telegram_user_id, challenge.chat_id
        binding.username, binding.display_name = challenge.username, challenge.display_name
        binding.active, binding.blocked = True, False
        binding.generation, binding.linked_at = secrets.token_hex(16), now()
        challenge.consumed_at = now()
        db.commit()
        return binding_data(binding)

    @router.delete('/link')
    def unlink(user=Depends(get_current_user), db: Session = Depends(get_db)):
        lock(db)
        revoke(db, db.query(models.TelegramBinding).filter_by(user_id=user.id).first())
        db.query(models.TelegramLinkChallenge).filter_by(user_id=user.id, consumed_at=None).update({'consumed_at':now()})
        db.commit()
        return {'unlinked':True}

    @router.get('/bot/me')
    def me(contact: bool = False, user=Depends(bot_actor), db: Session = Depends(get_db)):
        binding = user._telegram_binding
        if contact and binding.blocked:
            binding.blocked=False
            db.commit()
        return {'user_id':user.id, 'login':user.login, 'name':f'{user.last_name} {user.first_name}',
                'generation':binding.generation, 'blocked':binding.blocked}

    @router.post('/bot/blocked')
    def blocked(user=Depends(bot_actor), db: Session = Depends(get_db)):
        user._telegram_binding.blocked=True
        db.commit()
        return {'blocked':True}

    @router.post('/bot/unlink')
    def bot_unlink(user=Depends(bot_actor), db: Session = Depends(get_db)):
        revoke(db, user._telegram_binding)
        db.commit()
        return {'unlinked':True}

    return router
