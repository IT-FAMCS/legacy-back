"""Accountless, read-only trainee access. No imports from the main entrypoint."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db

SESSION_DAYS = 30
guest_bearer = HTTPBearer(auto_error=False)


class TraineeLogin(BaseModel):
    code: str = Field(..., min_length=1, max_length=128)


class TraineeCodeResponse(BaseModel):
    code: Optional[str] = None
    updated_at: Optional[datetime] = None


class TraineeSessionResponse(BaseModel):
    expires_at: datetime


class TraineeCardResponse(BaseModel):
    """Explicit allowlist: never serialize member access lists to guests."""
    id: int
    title: str
    content: Optional[str] = None
    category_id: int
    category_name: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


def create_trainee_router(*, get_current_user, can_manage_code, secret_key: str, algorithm: str):
    router = APIRouter(prefix="/api", tags=["Trainees"])

    def manager(user: models.User = Depends(get_current_user)):
        if not can_manage_code(user.position_ref):
            raise HTTPException(403, "Управлять кодом могут только администратор, председатель и его заместители")
        return user

    def trainee(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(guest_bearer),
        db: Session = Depends(get_db),
    ):
        error = HTTPException(401, "Доступ стажёра истёк или код изменился. Введите актуальный код.",
                              headers={"WWW-Authenticate": "Bearer"})
        if credentials is None:
            raise error
        try:
            payload = jwt.decode(credentials.credentials, secret_key, algorithms=[algorithm],
                                 options={"require_exp": True})
            if payload.get("type") != "trainee" or not isinstance(payload.get("code_version"), str):
                raise error
            current = db.get(models.TraineeCode, 1)
            if current is None or not secrets.compare_digest(current.version, payload["code_version"]):
                raise error
            return payload
        except (JWTError, ValueError, TypeError):
            raise error

    # Avoid browser/proxy caching of the shared code, session validation and private guest content.
    def no_cache(response: Response):
        response.headers["Cache-Control"] = "no-store"

    @router.get("/trainee-code", response_model=TraineeCodeResponse, dependencies=[Depends(no_cache)])
    def get_code(_user=Depends(manager), db: Session = Depends(get_db)):
        current = db.get(models.TraineeCode, 1)
        return {"code": current.code, "updated_at": current.updated_at} if current else {}

    @router.post("/trainee-code", response_model=TraineeCodeResponse, dependencies=[Depends(no_cache)])
    def rotate_code(_user=Depends(manager), db: Session = Depends(get_db)):
        values = {
            "id": 1,
            "code": "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(20)),
            "version": secrets.token_urlsafe(32),
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }
        # One SQLite statement: concurrent first generation/rotation stays atomic across workers.
        statement = insert(models.TraineeCode).values(**values)
        db.execute(statement.on_conflict_do_update(index_elements=["id"], set_=values))
        db.commit()
        return values

    @router.post("/trainee/login", response_model=schemas.Token, dependencies=[Depends(no_cache)])
    def login(data: TraineeLogin, db: Session = Depends(get_db)):
        current = db.get(models.TraineeCode, 1)
        supplied = data.code.strip().upper().encode("utf-8")
        if current is None or not secrets.compare_digest(current.code.encode("utf-8"), supplied):
            raise HTTPException(401, "Неверный код стажёра")
        now = datetime.now(timezone.utc)
        token = jwt.encode({
            "type": "trainee", "code_version": current.version,
            "iat": now, "exp": now + timedelta(days=SESSION_DAYS),
        }, secret_key, algorithm=algorithm)
        return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_DAYS * 86400}

    @router.get("/trainee/session", response_model=TraineeSessionResponse, dependencies=[Depends(no_cache)])
    def get_session(payload=Depends(trainee)):
        return {"expires_at": datetime.fromtimestamp(payload["exp"], timezone.utc)}

    @router.get("/trainee/categories", response_model=List[schemas.CategoryResponse],
                dependencies=[Depends(trainee), Depends(no_cache)])
    def categories(db: Session = Depends(get_db)):
        return (db.query(models.Category).join(models.Card)
                .filter(models.Card.trainee_access.is_(True)).distinct().order_by(models.Category.id).all())

    @router.get("/trainee/cards", response_model=List[TraineeCardResponse],
                dependencies=[Depends(trainee), Depends(no_cache)])
    def cards(db: Session = Depends(get_db)):
        rows = (db.query(models.Card, models.Category.name).join(models.Category)
                .filter(models.Card.trainee_access.is_(True)).order_by(models.Card.id).all())
        return [{**TraineeCardResponse.model_validate(card).model_dump(), "category_name": name}
                for card, name in rows]

    @router.get("/trainee/cards/{card_id}", response_model=TraineeCardResponse,
                dependencies=[Depends(trainee), Depends(no_cache)])
    def card(card_id: int, db: Session = Depends(get_db)):
        row = (db.query(models.Card, models.Category.name).join(models.Category)
               .filter(models.Card.id == card_id, models.Card.trainee_access.is_(True)).first())
        if row is None:
            raise HTTPException(404, "Карточка не найдена или недоступна стажёрам")
        item, name = row
        return {**TraineeCardResponse.model_validate(item).model_dump(), "category_name": name}

    return router
