"""One shared versioned responsibility board per event; never a profile field."""
import json
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session
import models
from database import get_db
from telegram_auth import now, replay, remember, no_store
from tasks import begin_task_write, display_name

class CreateZone(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    title: str=Field(min_length=1,max_length=200)
    people_required: int=Field(ge=1,le=1000)
    lead_user_id: Optional[int]=Field(default=None,gt=0)
    helper_ids: list[int]=Field(default_factory=list,max_length=1000)
    board_version: int=Field(ge=0)

class UpdateZone(CreateZone):
    title: Optional[str]=Field(default=None,min_length=1,max_length=200)
    people_required: Optional[int]=Field(default=None,ge=1,le=1000)
    helper_ids: Optional[list[int]]=Field(default=None,max_length=1000)
    version: int=Field(ge=1)
    @field_validator('title','people_required','helper_ids',mode='before')
    @classmethod
    def nonnull(cls,value):
        if value is None:raise ValueError('Поле не может быть пустым')
        return value

class BoardVersion(BaseModel):
    model_config=ConfigDict(extra='forbid')
    board_version: int=Field(ge=0)

class ZoneVersion(BoardVersion):
    version: int=Field(ge=1)


def person(user):
    return {'id':user.id,'name':display_name(user),'login':user.login,'telegram':user.telegram,
            'active':bool(user.is_active and not user.is_deactivated)}


def zone_data(zone):
    lead=person(zone.lead) if zone.lead else None
    helpers=sorted([person(link.user) for link in zone.helper_links],key=lambda u:u['id'])
    filled=int(bool(lead and lead['active']))+sum(int(p['active']) for p in helpers)
    return {'id':zone.id,'title':zone.title,'people_required':zone.people_required,
            'lead_user_id':zone.lead_user_id,'lead':lead,'helper_ids':[p['id'] for p in helpers],
            'helpers':helpers,'filled':filled,'closed':bool(lead and lead['active'] and filled>=zone.people_required),
            'version':zone.version}


def assert_no_zones(db,card_id):
    if db.query(models.ResponsibilityZone.id).filter_by(card_id=card_id).first():
        raise HTTPException(409,'Карточка содержит зоны ответственности. Сначала удалите их.')


def discard_empty_board(db,card_id):
    # A parent card can be removed after all zones were deleted. Do not keep
    # card-ID keyed private history that could attach to a future reused ID.
    assert_no_zones(db,card_id)
    db.query(models.ResponsibilityHistory).filter_by(card_id=card_id).delete(synchronize_session=False)
    db.query(models.ResponsibilityBoard).filter_by(card_id=card_id).delete(synchronize_session=False)


def create_zone_router(*,get_current_user,has_access_to_card,prefix='/api/cards',bot_mode=False):
    router=APIRouter(prefix=prefix,tags=['Responsibility zones'], dependencies=[Depends(no_store)] if bot_mode else [])
    suffix='/{card_id}/responsibility-zones'

    def event(db,card_id,user,write=False):
        if write:begin_task_write(db)
        card=db.get(models.Card,card_id)
        if not card or not has_access_to_card(user,card):raise HTTPException(404,'Мероприятие не найдено или недоступно')
        if card.category.name!='Мероприятия':raise HTTPException(422,'Зоны ответственности доступны только в мероприятиях')
        if write and not user.position_ref.can_edit_cards:raise HTTPException(403,'Нужно право редактирования карточки мероприятия')
        return card

    def board_data(db,card,user):
        board=db.get(models.ResponsibilityBoard,card.id)
        zones=[zone_data(zone) for zone in db.query(models.ResponsibilityZone).filter_by(card_id=card.id).order_by(models.ResponsibilityZone.id).all()]
        closed=sum(int(zone['closed']) for zone in zones)
        users=db.query(models.User).filter_by(is_active=True,is_deactivated=False).all() if user.position_ref.can_edit_cards else []
        return {'card':{'id':card.id,'title':card.title},'version':board.version if board else 0,
                'zones':zones,'total':len(zones),'closed':closed,'open':len(zones)-closed,
                'can_edit':bool(user.position_ref.can_edit_cards),'users':[person(u) for u in sorted(users,key=display_name)]}

    def check_board(db,card_id,version):
        board=db.get(models.ResponsibilityBoard,card_id)
        if version!=(board.version if board else 0):raise HTTPException(409,'Зоны уже изменились. Обновите список и повторите действие.')
        return board

    def touch(db,card_id,board):
        if board is None:db.add(models.ResponsibilityBoard(card_id=card_id,version=1,updated_at=now()))
        else:board.updated_at=now()

    def assignments(db,values,zone=None):
        lead=values.get('lead_user_id',zone.lead_user_id if zone else None)
        helpers=sorted(set(values.get('helper_ids',[link.user_id for link in zone.helper_links] if zone else [])))
        if lead is not None and lead in helpers:raise HTTPException(422,'Главный ответственный не может одновременно быть помогатором')
        old={link.user_id for link in zone.helper_links} if zone else set()
        if zone and zone.lead_user_id:old.add(zone.lead_user_id)
        for uid in helpers+([lead] if lead else []):
            user=db.get(models.User,uid)
            if user is None or (uid not in old and (not user.is_active or user.is_deactivated)):
                raise HTTPException(422,'Назначать можно только действующие аккаунты')
        return lead,helpers

    def apply(db,zone,values):
        lead,helpers=assignments(db,values,zone)
        if 'title' in values:zone.title=values['title']
        if 'people_required' in values:zone.people_required=values['people_required']
        zone.lead_user_id=lead;zone.lead=db.get(models.User,lead) if lead else None
        old={link.user_id:link for link in zone.helper_links}
        zone.helper_links=[old.get(uid) or models.ResponsibilityHelper(user_id=uid,user=db.get(models.User,uid)) for uid in helpers]
        zone.updated_at=now()

    def audit(db,card_id,zone_id,user,action,before,after):
        db.add(models.ResponsibilityHistory(card_id=card_id,zone_id=zone_id,actor_id=user.id,actor_name=display_name(user),
            action=action,before_json=json.dumps(before,ensure_ascii=False) if before else None,
            after_json=json.dumps(after,ensure_ascii=False) if after else None,created_at=now()))

    def finish(db,card,user):
        db.flush();result=board_data(db,card,user);remember(db,user,result);db.commit();return result

    if bot_mode:
        @router.get('')
        def events(user=Depends(get_current_user),db:Session=Depends(get_db)):
            cards=db.query(models.Card).join(models.Category).filter(models.Category.name=='Мероприятия').all()
            return [{'id':c.id,'title':c.title} for c in cards if has_access_to_card(user,c)]

    @router.get(suffix)
    def read(card_id:int,user=Depends(get_current_user),db:Session=Depends(get_db)):
        return board_data(db,event(db,card_id,user),user)

    @router.post(suffix,status_code=201)
    def create(card_id:int,data:CreateZone,user=Depends(get_current_user),db:Session=Depends(get_db)):
        card=event(db,card_id,user,True);cached=replay(db,user)
        if cached is not None:return cached
        board=check_board(db,card_id,data.board_version);values=data.model_dump(exclude={'board_version'})
        assignments(db,values)
        zone=models.ResponsibilityZone(card_id=card_id,title=data.title,people_required=data.people_required,version=1)
        db.add(zone);apply(db,zone,values);touch(db,card_id,board);db.flush()
        audit(db,card_id,zone.id,user,'create',None,zone_data(zone));return finish(db,card,user)

    @router.post(suffix+'/clear')
    def clear(card_id:int,data:BoardVersion,user=Depends(get_current_user),db:Session=Depends(get_db)):
        card=event(db,card_id,user,True);cached=replay(db,user)
        if cached is not None:return cached
        board=check_board(db,card_id,data.board_version)
        zones=db.query(models.ResponsibilityZone).filter_by(card_id=card_id).all();changed=[]
        for zone in zones:
            if zone.lead_user_id or zone.helper_links:
                before=zone_data(zone);zone.lead_user_id=None;zone.lead=None;zone.helper_links=[];zone.updated_at=now();changed.append((zone,before))
        if changed:touch(db,card_id,board)
        db.flush()
        for zone,before in changed:audit(db,card_id,zone.id,user,'clear',before,zone_data(zone))
        return finish(db,card,user)

    @router.get(suffix+'/history')
    def history(card_id:int,user=Depends(get_current_user),db:Session=Depends(get_db)):
        event(db,card_id,user)
        rows=db.query(models.ResponsibilityHistory).filter_by(card_id=card_id).order_by(models.ResponsibilityHistory.id.desc()).limit(100).all()
        return [{'id':r.id,'zone_id':r.zone_id,'actor_name':r.actor_name,'action':r.action,'created_at':r.created_at.isoformat()+'Z',
                 'before':json.loads(r.before_json) if r.before_json else None,'after':json.loads(r.after_json) if r.after_json else None} for r in rows]

    @router.patch(suffix+'/{zone_id}')
    def update(card_id:int,zone_id:int,data:UpdateZone,user=Depends(get_current_user),db:Session=Depends(get_db)):
        card=event(db,card_id,user,True);cached=replay(db,user)
        if cached is not None:return cached
        board=check_board(db,card_id,data.board_version);zone=db.get(models.ResponsibilityZone,zone_id)
        if not zone or zone.card_id!=card_id:raise HTTPException(404,'Зона не найдена')
        if zone.version!=data.version:raise HTTPException(409,'Зона уже изменена. Обновите список.')
        before=zone_data(zone);apply(db,zone,data.model_dump(exclude_unset=True,exclude={'version','board_version'}));touch(db,card_id,board)
        db.flush();audit(db,card_id,zone.id,user,'update',before,zone_data(zone));return finish(db,card,user)

    @router.delete(suffix+'/{zone_id}')
    def delete(card_id:int,zone_id:int,data:ZoneVersion,user=Depends(get_current_user),db:Session=Depends(get_db)):
        card=event(db,card_id,user,True);cached=replay(db,user)
        if cached is not None:return cached
        board=check_board(db,card_id,data.board_version);zone=db.get(models.ResponsibilityZone,zone_id)
        if not zone or zone.card_id!=card_id:raise HTTPException(404,'Зона не найдена')
        if zone.version!=data.version:raise HTTPException(409,'Зона уже изменена. Обновите список.')
        audit(db,card_id,zone.id,user,'delete',zone_data(zone),None);db.delete(zone);touch(db,card_id,board)
        return finish(db,card,user)

    return router
