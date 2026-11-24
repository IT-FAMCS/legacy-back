"""Versioned, explicit consents and owner-only payout templates shared by web and bot."""
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
import models
from database import get_db
from tasks import begin_task_write
from telegram_auth import bot_actor, no_store, replay, remember

SCHEMA_VERSION = '2026-11-24.2'

POLICY_DEFAULTS = {
    'operator': '',
    'address': '',
    'contact': 'https://famcs-legacy.by/user/admin',
    'processors': 'Основное место хранения данных сайта: Республика Беларусь. Доступ имеют только авторизованные пользователи сайта в пределах своих прав: сохранённые реквизиты доступны их владельцу, сведения бюджетной заявки — казначеям и руководству согласно полномочиям. Особенности использования Telegram описаны отдельно.',
    'retention': 'Срок хранения не ограничен заранее установленной календарной датой. Данные хранятся в течение использования сервиса и пока необходимы для заявленных целей обработки, с учётом права на удаление и отзыв согласия. Согласие действует до его отзыва или прекращения целей обработки. Сохранённые реквизиты удаляются при удалении записи или отзыве согласия на их хранение; для удаления остальных данных обратитесь к администратору.',
}


def policy():
    settings = {key: os.getenv('PERSONAL_DATA_'+key.upper(), '').strip() or value
                for key, value in POLICY_DEFAULTS.items()}
    sections = [
        'Цели: работа личного кабинета и студенческой организации, распределение задач и ответственности, учёт оборудования, рассмотрение бюджетных заявок и возврат расходов, уведомления.',
        'Данные: ФИО, логин и хеш пароля, дата рождения, курс, группа, форма обучения, должность, отделы, телефон и Telegram; подтверждённые идентификаторы Telegram и чата; задачи, комментарии, история действий и посещений, выдачи оборудования, участие в мероприятиях; суммы, описания бюджетных заявок, платёжные реквизиты и изображения чеков.',
        'Действия: сбор, запись, систематизация, хранение, использование, уточнение, предоставление уполномоченным участникам согласно их правам доступа, удаление. Обработка автоматизированная и с участием ответственных лиц. Бюджетные заявки и чеки доступны казначеям; сведения по заявкам — руководству в пределах прав доступа.',
        'Telegram: при использовании бота сообщения и вложения проходят через Telegram, в том числе за пределами Беларуси. Реквизиты заявки и чек передаются казначеям в личные чаты. Использование Telegram и сохранение реквизитов являются отдельными добровольными действиями; возможны риски доступа иностранных органов и отличий в защите данных. Не отправляйте CVV/CVC, PIN, коды подтверждения, пароли или фото банковской карты.',
        'Сохранённые реквизиты: отдельное добровольное согласие для повторного выбора при возврате расходов; до 3 записей (название и номер карты Белинвестбанка либо ЕРИП/номер счёта другого банка). Новые реквизиты заявки автоматически не сохраняются. Удаление шаблона не меняет реквизиты уже поданной заявки; их срок хранения определяется отдельно.',
    ]
    rights = [
        'Вы вправе получить сведения об обработке и предоставлении данных третьим лицам, потребовать исправления, прекращения обработки и удаления данных, отозвать согласие и обжаловать действия оператора в Национальный центр защиты персональных данных.',
        'Для реализации прав используйте контакт оператора ниже. Согласие можно отозвать в личном кабинете либо разделе «Бюджет → Персональные данные» бота. Отзыв согласия на сохранение реквизитов удаляет все сохранённые шаблоны. Отзыв основного согласия также удаляет шаблоны и закрывает доступ к сайту до нового согласия; для удаления остальных данных направьте обращение оператору. Обработка при наличии иного законного основания может продолжаться в необходимом объёме.',
        'Согласие добровольно. Отказ от основного согласия не позволяет пользоваться личным кабинетом. Отказ от сохранения реквизитов не препятствует работе с заявками: реквизиты можно вводить каждый раз.',
    ]
    result = dict(**settings, configured=all(settings[key] for key in ('contact','processors','retention')), sections=sections, rights=rights)
    result['version'] = hashlib.sha256(json.dumps([SCHEMA_VERSION,result],ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return result


def consent_current(db, uid, scope, current=None):
    current = current or policy()
    if not current['configured']: return False
    return db.query(models.PersonalDataConsent.id).filter_by(user_id=uid,scope=scope,version=current['version'],revoked_at=None).first() is not None


def require_service_consent(db, uid):
    if not consent_current(db,uid,'service'):
        raise HTTPException(403,'Для отправки заявки дайте актуальное согласие на обработку данных в разделе «Бюджет». Черновик сохранён.')


def require_saved_consent(db, uid):
    current=policy()
    if not consent_current(db,uid,'service',current) or not consent_current(db,uid,'saved_details',current):
        raise HTTPException(403,'Сначала ознакомьтесь с условиями и дайте согласие на сохранение реквизитов.')


def resolve_saved(db, uid, rid):
    require_saved_consent(db,uid)
    row=db.query(models.SavedPayoutDetail).filter_by(id=rid,user_id=uid).first()
    if row is None: raise HTTPException(404,'Сохранённые реквизиты не найдены. Выберите другие или введите новые.')
    return row.details


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)

class ConsentInput(Input):
    scope: Literal['service','saved_details']
    version: str = Field(min_length=1,max_length=64)

class PayoutInput(Input):
    label: str = Field(min_length=1,max_length=60)
    details: str = Field(min_length=1,max_length=500)


def create_personal_data_router(get_current_user):
    router=APIRouter(tags=['Personal data'],dependencies=[Depends(no_store)])
    @router.get('/api/personal-data/policy')
    def public_policy(): return policy()

    def install(prefix, actor):
        if prefix != '/api':
            @router.get(prefix+'/personal-data/policy')
            def actor_policy(user=Depends(actor)): return policy()

        @router.get(prefix+'/personal-data/me')
        def status(user=Depends(actor),db:Session=Depends(get_db)):
            current=policy()
            return dict(policy=current,service=consent_current(db,user.id,'service',current),
                        saved_details=consent_current(db,user.id,'saved_details',current))

        @router.post(prefix+'/personal-data/consents')
        def accept(data:ConsentInput,user=Depends(actor),db:Session=Depends(get_db)):
            begin_task_write(db)
            previous=replay(db,user)
            if previous is not None:return previous
            current=policy()
            if not current['configured']:raise HTTPException(503,'Оператор ещё не заполнил условия обработки данных. Обратитесь к администратору.')
            if data.version!=current['version']:raise HTTPException(409,'Условия изменились. Ознакомьтесь с новой версией.')
            if data.scope=='saved_details' and not consent_current(db,user.id,'service',current):
                raise HTTPException(403,'Сначала дайте основное согласие.')
            if not consent_current(db,user.id,data.scope,current):
                db.add(models.PersonalDataConsent(user_id=user.id,scope=data.scope,version=current['version'],
                    policy_snapshot=json.dumps(current,ensure_ascii=False),source='telegram' if getattr(user,'_telegram_binding',None) else 'web'))
            result={'ok':True};remember(db,user,result);db.commit();return result

        @router.delete(prefix+'/personal-data/consents/{scope}')
        def revoke(scope:Literal['service','saved_details'],user=Depends(actor),db:Session=Depends(get_db)):
            begin_task_write(db)
            previous=replay(db,user)
            if previous is not None:return previous
            scopes=['service','saved_details'] if scope=='service' else ['saved_details']
            db.query(models.PersonalDataConsent).filter(models.PersonalDataConsent.user_id==user.id,
                models.PersonalDataConsent.scope.in_(scopes),models.PersonalDataConsent.revoked_at.is_(None)).update({'revoked_at':datetime.now(timezone.utc).replace(tzinfo=None)},synchronize_session=False)
            db.query(models.SavedPayoutDetail).filter_by(user_id=user.id).delete()
            result={'ok':True};remember(db,user,result);db.commit();return result

        @router.get(prefix+'/payout-details')
        def saved(user=Depends(actor),db:Session=Depends(get_db)):
            if not consent_current(db,user.id,'service') or not consent_current(db,user.id,'saved_details'):return []
            return [dict(id=row.id,label=row.label,details=row.details) for row in db.query(models.SavedPayoutDetail).filter_by(user_id=user.id).order_by(models.SavedPayoutDetail.id).all()]

        @router.post(prefix+'/payout-details',status_code=201)
        def add(data:PayoutInput,user=Depends(actor),db:Session=Depends(get_db)):
            begin_task_write(db)
            previous=replay(db,user)
            if previous is not None:
                require_saved_consent(db,user.id)
                existing=db.query(models.SavedPayoutDetail).filter_by(id=previous['id'],user_id=user.id).first()
                if existing is None:raise HTTPException(409,'Эти реквизиты уже удалены. Обновите список.')
                return dict(id=existing.id,label=existing.label,details=existing.details)
            require_saved_consent(db,user.id)
            if db.query(models.SavedPayoutDetail).filter_by(user_id=user.id).count()>=3:
                raise HTTPException(409,'Можно сохранить максимум 3 реквизита. Удалите ненужные.')
            row=models.SavedPayoutDetail(user_id=user.id,**data.model_dump());db.add(row);db.flush()
            result=dict(id=row.id,label=row.label,details=row.details)
            # A replay receipt must not retain deleted bank details.
            remember(db,user,dict(id=row.id));db.commit();return result

        @router.delete(prefix+'/payout-details/{rid}')
        def remove(rid:int,user=Depends(actor),db:Session=Depends(get_db)):
            begin_task_write(db)
            previous=replay(db,user)
            if previous is not None:return previous
            row=db.query(models.SavedPayoutDetail).filter_by(id=rid,user_id=user.id).first()
            if row is None:raise HTTPException(404,'Реквизиты не найдены.')
            db.delete(row)
            result={'ok':True};remember(db,user,result);db.commit();return result
    install('/api',get_current_user)
    install('/api/telegram/bot',bot_actor)
    return router
