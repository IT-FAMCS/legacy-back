"""Private member warnings: explicit viewers, restricted issuers, immutable audit."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, update
from sqlalchemy.orm import Session
import models
from database import get_db


class Reason(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    reason: str = Field(min_length=1, max_length=4000)


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def full_name(user):
    return " ".join(value for value in (user.last_name, user.first_name, user.middle_name) if value)


def warning_data(entry):
    return {"id": entry.id, "user_id": entry.user_id, "reason": entry.reason,
            "issued_at": entry.issued_at,
            "issued_by": {"login": entry.issued_by_login, "name": entry.issued_by_name},
            "removed_at": entry.removed_at, "removal_reason": entry.removal_reason,
            "removed_by": {"login": entry.removed_by_login, "name": entry.removed_by_name} if entry.removed_at else None}


def create_warning_router(*, get_current_user, can_view, can_manage):
    def no_cache(response: Response):
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(prefix="/api", tags=["Warnings"], dependencies=[Depends(no_cache)])

    def viewer(user=Depends(get_current_user)):
        if not can_view(user.position_ref):
            raise HTTPException(403, "Нет доступа к предупреждениям")
        return user

    def manager(user=Depends(get_current_user)):
        if not can_manage(user.position_ref):
            raise HTTPException(403, "Выдавать и снимать предупреждения могут только председатель, его заместители и админ")
        return user

    def target(db, user_id):
        user = db.get(models.User, user_id)
        if user is None:
            raise HTTPException(404, "Пользователь не найден")
        return user

    @router.get("/warnings/permissions")
    def permissions(user=Depends(get_current_user)):
        return {"can_view": can_view(user.position_ref), "can_manage": can_manage(user.position_ref)}

    @router.get("/warnings/counts")
    def counts(_user=Depends(viewer), db: Session = Depends(get_db)):
        rows = db.query(models.MemberWarning.user_id, func.count(models.MemberWarning.id)).filter(
            models.MemberWarning.removed_at.is_(None)).group_by(models.MemberWarning.user_id).all()
        return {str(user_id): count for user_id, count in rows}

    @router.get("/users/{user_id}/warnings")
    def history(user_id: int, _user=Depends(viewer), db: Session = Depends(get_db)):
        target(db, user_id)
        rows = db.query(models.MemberWarning).filter_by(user_id=user_id).order_by(models.MemberWarning.id.desc()).all()
        return [warning_data(row) for row in rows]

    @router.post("/users/{user_id}/warnings", status_code=201)
    def issue(user_id: int, data: Reason, actor=Depends(manager), db: Session = Depends(get_db)):
        target(db, user_id)
        entry = models.MemberWarning(user_id=user_id, reason=data.reason, issued_at=now_utc(),
            issued_by_login=actor.login, issued_by_name=full_name(actor))
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return warning_data(entry)

    @router.post("/warnings/{warning_id}/remove")
    def remove(warning_id: int, data: Reason, actor=Depends(manager), db: Session = Depends(get_db)):
        entry = db.get(models.MemberWarning, warning_id)
        if entry is None:
            raise HTTPException(404, "Предупреждение не найдено")
        result = db.execute(update(models.MemberWarning).where(models.MemberWarning.id == warning_id,
            models.MemberWarning.removed_at.is_(None)).values(removed_at=now_utc(), removal_reason=data.reason,
                removed_by_login=actor.login, removed_by_name=full_name(actor)).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            db.rollback()
            raise HTTPException(409, "Предупреждение уже снято. Обновите историю")
        db.commit()
        db.refresh(entry)
        return warning_data(entry)

    return router
