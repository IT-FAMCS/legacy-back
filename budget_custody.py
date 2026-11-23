"""Personal holdings: ledger movement plus auditable manual adjustments."""
from decimal import Decimal
from typing import Literal
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
import models
from database import get_db
from tasks import begin_task_write
from budget import Input, money, person_name


class BalanceSet(Input):
    account: Literal['cash', 'card']
    amount: Decimal
    expected_balance: Decimal

    @classmethod
    def validate_money(cls, value):
        if not value.is_finite() or abs(value) > Decimal('9999999999.99') or value != value.quantize(Decimal('.01')):
            raise HTTPException(422, 'Некорректная сумма BYN')


def holdings(db):
    result = {}
    def account(uid):
        return result.setdefault(uid, dict(cash=Decimal(0), card=Decimal(0)))
    for row in db.query(models.BudgetEntry).filter(models.BudgetEntry.deleted_at.is_(None), models.BudgetEntry.treasurer_id.is_not(None)).all():
        account(row.treasurer_id)[row.account] += row.amount if row.direction == 'income' else -row.amount
    for row in db.query(models.BudgetCustodyAdjustment).all():
        account(row.user_id)[row.account] += row.amount
    return result


def install_custody_routes(router, manager, is_executive):
    @router.get('/api/budget/treasurers')
    def treasurers(user=Depends(manager), db: Session=Depends(get_db)):
        balances = holdings(db)
        people = db.query(models.User).all()
        return [dict(user_id=person.id, name=person_name(person) or person.login, login=person.login,
                     is_treasurer=bool(person.position_ref.is_kaznachey),
                     can_select=bool(person.position_ref.is_kaznachey and person.is_active and not person.is_deactivated),
                     can_edit=bool(is_executive(user.position_ref) or person.id == user.id),
                     balances={k: money(v) for k,v in balances.get(person.id,dict(cash=Decimal(0),card=Decimal(0))).items()})
                for person in people if person.position_ref.is_kaznachey or person.id in balances]

    @router.put('/api/budget/treasurers/{uid}/balance')
    def set_balance(uid: int, data: BalanceSet, user=Depends(manager), db: Session=Depends(get_db)):
        if not (is_executive(user.position_ref) or user.id == uid):
            raise HTTPException(403, 'Можно изменять только свой остаток')
        BalanceSet.validate_money(data.amount); BalanceSet.validate_money(data.expected_balance)
        begin_task_write(db)
        person = db.get(models.User, uid)
        if person is None: raise HTTPException(404, 'Казначей не найден')
        balances = holdings(db)
        if not person.position_ref.is_kaznachey and uid not in balances:
            raise HTTPException(422, 'У пользователя нет операций бюджета')
        current = balances.get(uid, {}).get(data.account, Decimal(0))
        if current != data.expected_balance:
            raise HTTPException(409, 'Остаток изменился. Обновите данные и повторите изменение.')
        delta = data.amount-current
        if abs(delta) > Decimal('9999999999.99'): raise HTTPException(422, 'Слишком большая корректировка')
        db.add(models.BudgetCustodyAdjustment(user_id=uid,account=data.account,amount=delta,
               balance_before=current,balance_after=data.amount,created_by=user.id))
        db.commit()
        return dict(ok=True)


def validate_treasurer(db, uid, existing=None):
    if uid is None: return
    person = db.get(models.User, uid)
    if person is None or (uid != existing and (not person.position_ref.is_kaznachey or not person.is_active or person.is_deactivated)):
        raise HTTPException(422, 'Выберите действующего казначея или оставьте поле пустым')
