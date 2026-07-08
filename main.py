import os

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import Optional, List, Dict
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import models
import schemas

from database import engine, get_db, SessionLocal

# ============ CONFIGURATION ============
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = int(os.getenv("ACCESS_TOKEN_EXPIRE_DAYS", "30"))

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Security
security = HTTPBearer()

app = FastAPI(
    title="Student Organization Management System",
    description="API with role-based access control and positions table",
    version="3.0.0"
)

# Create all tables
models.Base.metadata.create_all(bind=engine)


def ensure_schema_upgrades() -> None:
    """Add columns introduced after the initial create_all to already-existing
    SQLite databases. create_all() only creates missing tables, it never
    alters existing ones — without this, an existing app.db would keep
    missing e.g. users.password_changed_at and error out on first use."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as conn:
        existing_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()}
        if "password_changed_at" not in existing_columns:
            conn.exec_driver_sql("ALTER TABLE users ADD COLUMN password_changed_at DATETIME")


ensure_schema_upgrades()

# ============ CORS CONFIGURATION ============
# Список разрешённых origin'ов берётся из ALLOWED_ORIGINS (через запятую).
# В проде фронт и API на одном домене (nginx проксирует /api), поэтому CORS по сути
# не задействуется, но оставляем настраиваемым — на случай отдельного домена для API.
_default_origins = (
    "http://localhost:5173,http://127.0.0.1:5173,"
    "http://localhost:3000,http://127.0.0.1:3000"
)
_allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", _default_origins).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ HELPER FUNCTIONS ============

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def can_manage_positions(position: models.Position) -> bool:
    """Check if position can manage other positions"""
    return position.can_manage_positions


def can_register_users(position: models.Position) -> bool:
    return position.can_register_users


def normalize_position_name(position: models.Position) -> str:
    return (position.name or "").strip().lower()


def is_admin_chair_or_deputy(position: models.Position) -> bool:
    name = normalize_position_name(position)
    return (
        name == "admin"
        or name == "админ"
        or name == "председатель"
        or name == "председатель студсовета"
        or name.startswith("заместитель председателя")
        or name.startswith("зам. председателя")
    )


def is_secretary(position: models.Position) -> bool:
    return normalize_position_name(position) == "секретарь"


def is_department_lead(position: models.Position) -> bool:
    name = normalize_position_name(position)
    return (
        name == "руководитель отдела"
        or name == "руководитель отдела/направления"
        or name == "заместитель руководителя отдела"
        or name.startswith("заместитель руководителя")
    )


def can_edit_categories(position: models.Position) -> bool:
    return bool(position.can_edit_categories)


def can_delete_categories(position: models.Position) -> bool:
    # Удаление тем/категорий отключено по требованиям. Функция оставлена
    # для совместимости со схемами/старыми данными, но API удаления запрещает действие.
    return False


def can_edit_cards(position: models.Position) -> bool:
    return bool(position.can_edit_cards) or is_department_lead(position)


def can_delete_cards(position: models.Position) -> bool:
    return bool(position.can_delete_cards) or is_admin_chair_or_deputy(position)


def can_edit_any_user(position: models.Position) -> bool:
    return bool(position.can_edit_any_user)


def can_view_card_activity(position: models.Position) -> bool:
    """Card create/update/delete activity log is admin/chair/deputy-only, not
    the broader can_edit_any_user audience (secretaries, dept heads, etc.)."""
    return is_admin_chair_or_deputy(position)


def can_deactivate_users(position: models.Position) -> bool:
    return is_secretary(position) or is_admin_chair_or_deputy(position)


def can_edit_position_permissions(position: models.Position) -> bool:
    return is_admin_chair_or_deputy(position)


def can_manage_departments(position: models.Position) -> bool:
    """Check if position can manage departments"""
    return can_deactivate_users(position)


def has_higher_authority(manager_position: models.Position, target_position: models.Position) -> bool:
    """Check if manager has higher authority (lower level = higher authority)"""
    return manager_position.hierarchy_level < target_position.hierarchy_level


def has_access_to_card(user: models.User, card: models.Card) -> bool:
    # Admin has access to all cards
    if normalize_position_name(user.position_ref) in {"admin", "админ"}:
        return True
    
    if not card.access_positions and not card.access_logins:
        return True
    
    if card.access_positions:
        allowed_positions = [p.strip().lower() for p in card.access_positions.split(",")]
        if normalize_position_name(user.position_ref) in allowed_positions:
            return True
    
    if card.access_logins:
        allowed_logins = [l.strip().lower() for l in card.access_logins.split(",")]
        if user.login.lower() in allowed_logins:
            return True
    
    return False


def ensure_runtime_permissions() -> None:
    """Safely update default role permissions without recreating or clearing the DB."""
    db = SessionLocal()
    try:
        updates = {
            "admin": {
                "can_register_users": True,
                "can_edit_categories": True,
                "can_edit_cards": True,
                "can_delete_cards": True,
                "can_edit_any_user": True,
                "can_manage_positions": True,
            },
            "председатель": {
                "can_register_users": True,
                "can_edit_categories": True,
                "can_edit_cards": True,
                "can_delete_cards": True,
                "can_edit_any_user": True,
                "can_manage_positions": True,
            },
            "председатель студсовета": {
                "can_register_users": True,
                "can_edit_categories": True,
                "can_edit_cards": True,
                "can_delete_cards": True,
                "can_edit_any_user": True,
                "can_manage_positions": True,
            },
            "заместитель председателя": {
                "can_register_users": True,
                "can_edit_categories": True,
                "can_edit_cards": True,
                "can_delete_cards": True,
                "can_edit_any_user": True,
                "can_manage_positions": True,
            },
            "секретарь": {
                "can_register_users": True,
                "can_edit_categories": True,
                "can_edit_cards": True,
                "can_delete_cards": False,
                "can_edit_any_user": True,
                "can_manage_positions": False,
            },
            "руководитель отдела": {"can_edit_cards": True},
            "руководитель отдела/направления": {"can_edit_cards": True},
            "заместитель руководителя отдела": {"can_edit_cards": True},
        }

        for position_name, values in updates.items():
            position = db.query(models.Position).filter(models.Position.name == position_name).first()
            if not position:
                continue
            for field, value in values.items():
                setattr(position, field, value)

        # Удаление тем отключено для всех ролей, чтобы старые флаги не открывали доступ.
        for position in db.query(models.Position).all():
            position.can_delete_categories = False

        db.commit()
    finally:
        db.close()


ensure_runtime_permissions()


# ============ DEPENDENCIES ============

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> models.User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверные учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        login: str = payload.get("sub")
        if login is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.query(models.User).filter(models.User.login == login).first()
    if user is None:
        raise credentials_exception
    
    if user.is_deactivated or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт деактивирован"
        )
    
    return user


# ============ AUTH ENDPOINTS ============

@app.post("/api/login", response_model=schemas.Token, summary="User login")
async def login(user_data: schemas.UserLogin, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.login == user_data.login).first()
    
    if not user or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный логин или пароль",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if user.is_deactivated or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аккаунт деактивирован"
        )
    
    user.last_login = datetime.utcnow()
    db.commit()
    
    access_token = create_access_token(
        data={"sub": user.login},
        expires_delta=timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    }


@app.post("/api/logout", summary="User logout")
async def logout(current_user: models.User = Depends(get_current_user)):
    return {"message": "Успешный выход"}


# ============ POSITION ENDPOINTS ============

@app.get("/api/positions", response_model=List[schemas.PositionResponse], summary="Get all positions")
async def get_positions(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all positions"""
    positions = db.query(models.Position).order_by(models.Position.hierarchy_level).all()
    return positions


@app.post("/api/positions", response_model=schemas.PositionResponse, status_code=status.HTTP_201_CREATED, summary="Create position")
async def create_position(
    position_data: schemas.PositionCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new position. Only available to positions with can_manage_positions=True.
    By default: admin, председатель, заместитель председателя, председатель студсовета, секретарь
    """
    if not current_user.position_ref.can_manage_positions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для управления должностями"
        )
    
    existing = db.query(models.Position).filter(models.Position.name == position_data.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Должность с таким названием уже существует"
        )
    
    new_position = models.Position(**position_data.dict())
    db.add(new_position)
    db.commit()
    db.refresh(new_position)
    
    return new_position


@app.get("/api/positions/{position_id}", response_model=schemas.PositionResponse, summary="Get position by ID")
async def get_position(
    position_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    position = db.query(models.Position).filter(models.Position.id == position_id).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Должность не найдена"
        )
    return position


@app.put("/api/positions/{position_id}", response_model=schemas.PositionResponse, summary="Update position")
async def update_position(
    position_id: int,
    position_update: schemas.PositionUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_edit_position_permissions(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования доступов должностей"
        )
    
    position = db.query(models.Position).filter(models.Position.id == position_id).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Должность не найдена"
        )
    
    update_data = position_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(position, field, value)
    
    db.commit()
    db.refresh(position)
    return position


@app.delete("/api/positions/{position_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete position")
async def delete_position(
    position_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not current_user.position_ref.can_manage_positions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления должностей"
        )
    
    position = db.query(models.Position).filter(models.Position.id == position_id).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Должность не найдена"
        )
    
    # Check if any users have this position
    users_with_position = db.query(models.User).filter(models.User.position_id == position_id).count()
    if users_with_position > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить должность, пока есть пользователи с этой должностью"
        )
    
    db.delete(position)
    db.commit()
    return None


# ============ USER MANAGEMENT ENDPOINTS ============

def get_user_departments(user: models.User, db: Session) -> List[dict]:
    """Get list of departments for a user"""
    user_depts = db.query(models.UserDepartment).filter(
        models.UserDepartment.user_id == user.id
    ).all()
    return [{"id": ud.department.id, "name": ud.department.name} for ud in user_depts]




def validate_user_registration_data(
    user_data: schemas.UserRegister,
    current_user: models.User,
    db: Session
) -> Dict[str, object]:
    existing_user = db.query(models.User).filter(models.User.login == user_data.login).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Пользователь с логином '{user_data.login}' уже существует"
        )

    position = db.query(models.Position).filter(models.Position.name == user_data.position).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Должность '{user_data.position}' не найдена"
        )

    if not has_higher_authority(current_user.position_ref, position):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Недостаточно прав для назначения должности '{user_data.position}'"
        )

    validated_department_ids = []
    if user_data.department_ids:
        seen_department_ids = set()
        for dept_id in user_data.department_ids:
            if dept_id in seen_department_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"У пользователя '{user_data.login}' отдел с id={dept_id} указан несколько раз"
                )
            seen_department_ids.add(dept_id)

            dept = db.query(models.Department).filter(models.Department.id == dept_id).first()
            if not dept:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Отдел с id={dept_id} не найден для пользователя '{user_data.login}'"
                )
            validated_department_ids.append(dept_id)

    return {
        "position": position,
        "department_ids": validated_department_ids
    }


def create_user_with_relations(
    user_data: schemas.UserRegister,
    position: models.Position,
    department_ids: List[int],
    db: Session
) -> models.User:
    new_user = models.User(
        login=user_data.login,
        password_hash=hash_password(user_data.password),
        password_changed_at=datetime.utcnow(),
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        middle_name=user_data.middle_name,
        birth_date=user_data.birth_date,
        course=user_data.course,
        group=user_data.group,
        position_id=position.id,
        telegram=user_data.telegram,
    )

    db.add(new_user)
    db.flush()

    for dept_id in department_ids:
        user_dept = models.UserDepartment(user_id=new_user.id, department_id=dept_id)
        db.add(user_dept)

    return new_user

def format_user_response(user: models.User, db: Session) -> dict:
    """Format user response with departments list"""
    return {
        **user.__dict__,
        "position_name": user.position_ref.name,
        "departments": get_user_departments(user, db)
    }


@app.post("/api/register", response_model=schemas.UserResponse, status_code=status.HTTP_201_CREATED, summary="Register new user")
async def register_user(
    user_data: schemas.UserRegister,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_register_users(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для регистрации пользователей"
        )

    validation_result = validate_user_registration_data(user_data, current_user, db)
    new_user = create_user_with_relations(
        user_data=user_data,
        position=validation_result["position"],
        department_ids=validation_result["department_ids"],
        db=db
    )

    db.commit()
    db.refresh(new_user)

    return format_user_response(new_user, db)


@app.post("/api/register/bulk", response_model=List[schemas.UserResponse], status_code=status.HTTP_201_CREATED, summary="Register multiple users")
async def register_users_bulk(
    users_data: schemas.BulkUserRegister,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_register_users(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для регистрации пользователей"
        )

    payload_logins = [user.login for user in users_data.users]
    duplicated_logins = sorted({login for login in payload_logins if payload_logins.count(login) > 1})
    if duplicated_logins:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"В запросе повторяются логины: {', '.join(duplicated_logins)}"
        )

    prepared_users = []
    for user_data in users_data.users:
        validation_result = validate_user_registration_data(user_data, current_user, db)
        prepared_users.append({
            "user_data": user_data,
            "position": validation_result["position"],
            "department_ids": validation_result["department_ids"]
        })

    created_users = []
    try:
        for item in prepared_users:
            new_user = create_user_with_relations(
                user_data=item["user_data"],
                position=item["position"],
                department_ids=item["department_ids"],
                db=db
            )
            created_users.append(new_user)

        db.commit()

        for user in created_users:
            db.refresh(user)

        return [format_user_response(user, db) for user in created_users]
    except Exception:
        db.rollback()
        raise


@app.get("/api/users", response_model=List[schemas.UserResponse], summary="Get users list")
async def get_users(
    department_id: Optional[int] = None,
    department: Optional[str] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get users list - available to all authenticated users.

    department_id is the main filter. department is kept as a backward-compatible
    alias and may contain either an id or a department name.
    """
    query = db.query(models.User).join(models.Position)

    resolved_department_id = department_id
    if resolved_department_id is None and department:
        if department.isdigit():
            resolved_department_id = int(department)
        else:
            dept = db.query(models.Department).filter(models.Department.name == department).first()
            if dept:
                resolved_department_id = dept.id
            else:
                return []

    if resolved_department_id:
        query = query.join(models.UserDepartment).filter(
            models.UserDepartment.department_id == resolved_department_id
        )
    
    users = query.order_by(models.User.last_name, models.User.first_name, models.User.login).all()
    result = []
    for user in users:
        result.append(format_user_response(user, db))
    return result


@app.get("/api/user/me", response_model=schemas.UserResponse, summary="Get current user info")
async def get_current_user_info(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return format_user_response(current_user, db)


@app.put("/api/user/change/{target_login}", response_model=schemas.UserResponse, summary="Update user data")
async def update_user_data(
    target_login: str,
    user_update: schemas.UserUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # Users cannot edit themselves
    if target_login == current_user.login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пользователи не могут редактировать себя. Обратитесь к администратору."
        )
    
    target_user = db.query(models.User).filter(models.User.login == target_login).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    
    # Check permissions
    if can_edit_any_user(current_user.position_ref):
        # Admin-level users can edit anyone
        if user_update.position:
            new_position = db.query(models.Position).filter(models.Position.name == user_update.position).first()
            if not new_position:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Должность не найдена"
                )
            if not has_higher_authority(current_user.position_ref, new_position):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Недостаточно прав для изменения этой должности"
                )
            target_user.position_id = new_position.id
        
        update_data = user_update.dict(exclude_unset=True, exclude={"position", "department_ids"})
        for field, value in update_data.items():
            if value is not None:
                setattr(target_user, field, value)
        
        # Update departments if provided
        if user_update.department_ids is not None:
            # Remove existing department assignments
            db.query(models.UserDepartment).filter(
                models.UserDepartment.user_id == target_user.id
            ).delete()
            
            # Add new department assignments
            for dept_id in user_update.department_ids:
                dept = db.query(models.Department).filter(models.Department.id == dept_id).first()
                if dept:
                    user_dept = models.UserDepartment(user_id=target_user.id, department_id=dept_id)
                    db.add(user_dept)
        
        db.commit()
        db.refresh(target_user)
        return format_user_response(target_user, db)
    
    # No permissions
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Недостаточно прав для редактирования пользователей"
    )


@app.put("/api/user/me/change-password", summary="Change current user password")
async def change_own_password(
    password_data: schemas.OwnPasswordChange,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Change current user's password after checking the current password."""
    user = db.query(models.User).filter(models.User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    if not verify_password(password_data.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текущий пароль указан неверно"
        )

    user.password_hash = hash_password(password_data.new_password)
    user.password_changed_at = datetime.utcnow()
    db.commit()
    return {"message": "Пароль успешно изменен"}


@app.put("/api/user/change-password/{target_login}", response_model=schemas.UserResponse, summary="Change user password")
async def change_user_password(
    target_login: str,
    password_data: schemas.PasswordChange,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Change user password. Only available to users with can_edit_any_user permission.
    Password must be provided in the request body as {"password": "new_password"}
    """
    if not can_edit_any_user(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для смены пароля пользователей"
        )
    
    target_user = db.query(models.User).filter(models.User.login == target_login).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    
    target_user.password_hash = hash_password(password_data.password)
    target_user.password_changed_at = datetime.utcnow()
    db.commit()
    db.refresh(target_user)
    return format_user_response(target_user, db)



def set_user_deactivation_status(
    target_login: str,
    deactivated: bool,
    current_user: models.User,
    db: Session,
) -> dict:
    if not can_deactivate_users(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для деактивации пользователей"
        )

    if target_login == current_user.login:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нельзя деактивировать собственный аккаунт"
        )

    target_user = db.query(models.User).filter(models.User.login == target_login).first()
    if not target_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")

    target_user.is_deactivated = deactivated
    target_user.is_active = not deactivated
    db.commit()
    db.refresh(target_user)
    return format_user_response(target_user, db)


@app.put("/api/users/{target_login}/deactivate", response_model=schemas.UserResponse, summary="Deactivate user")
async def deactivate_user(
    target_login: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return set_user_deactivation_status(target_login, True, current_user, db)


@app.put("/api/users/{target_login}/activate", response_model=schemas.UserResponse, summary="Activate user")
async def activate_user(
    target_login: str,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return set_user_deactivation_status(target_login, False, current_user, db)


# ============ CATEGORY ENDPOINTS ============

@app.post("/api/categories", response_model=schemas.CategoryResponse, status_code=status.HTTP_201_CREATED, summary="Create category")
async def create_category(
    category: schemas.CategoryCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_edit_categories(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для создания категорий"
        )
    
    existing = db.query(models.Category).filter(models.Category.name == category.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Категория с таким названием уже существует"
        )
    
    new_category = models.Category(name=category.name, description=category.description)
    db.add(new_category)
    db.commit()
    db.refresh(new_category)
    return new_category


@app.get("/api/categories", response_model=List[schemas.CategoryResponse], summary="Get all categories")
async def get_categories(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return db.query(models.Category).all()


@app.get("/api/categories/{category_id}", response_model=schemas.CategoryResponse, summary="Get category by ID")
async def get_category(
    category_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
    
    visit = models.CategoryVisit(user_id=current_user.id, category_id=category_id)
    db.add(visit)
    db.commit()
    
    return category


@app.put("/api/categories/{category_id}", response_model=schemas.CategoryResponse, summary="Update category")
async def update_category(
    category_id: int,
    category_update: schemas.CategoryUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_edit_categories(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования категорий"
        )
    
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
    
    update_data = category_update.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(category, field, value)
    
    db.commit()
    db.refresh(category)
    return category


@app.delete("/api/categories/{category_id}", status_code=status.HTTP_405_METHOD_NOT_ALLOWED, summary="Delete category disabled")
async def delete_category(
    category_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Удаление тем отключено"
    )


# ============ CARD ENDPOINTS ============

def log_activity(
    db: Session,
    user_id: int,
    action: str,
    entity_type: str,
    entity_id: Optional[int],
    entity_title: Optional[str],
    details: Optional[str] = None,
) -> None:
    db.add(models.ActivityLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_title=entity_title,
        details=details,
    ))


def describe_card_changes(card: models.Card, update_data: dict, db: Session) -> Optional[str]:
    """Human-readable Russian summary of what changed in a card update."""
    parts = []

    if "title" in update_data and update_data["title"] != card.title:
        parts.append(f"заголовок: «{card.title}» → «{update_data['title']}»")

    if "category_id" in update_data and update_data["category_id"] != card.category_id:
        old_category = db.query(models.Category).filter(models.Category.id == card.category_id).first()
        new_category = db.query(models.Category).filter(models.Category.id == update_data["category_id"]).first()
        parts.append(
            f"тема: «{old_category.name if old_category else '?'}» → "
            f"«{new_category.name if new_category else '?'}»"
        )

    if "access_positions" in update_data and update_data["access_positions"] != card.access_positions:
        parts.append("доступ по должностям")

    if "access_logins" in update_data and update_data["access_logins"] != card.access_logins:
        parts.append("доступ по логинам")

    if "content" in update_data and update_data["content"] != card.content:
        parts.append("содержимое")

    return "Изменено: " + ", ".join(parts) if parts else None


@app.post("/api/cards", response_model=schemas.CardResponse, status_code=status.HTTP_201_CREATED, summary="Create card")
async def create_card(
    card: schemas.CardCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_edit_cards(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для создания карточек"
        )

    category = db.query(models.Category).filter(models.Category.id == card.category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")

    new_card = models.Card(
        title=card.title,
        content=card.content,
        category_id=card.category_id,
        access_positions=card.access_positions,
        access_logins=card.access_logins
    )

    db.add(new_card)
    db.flush()
    log_activity(db, current_user.id, "create", "card", new_card.id, new_card.title)
    db.commit()
    db.refresh(new_card)

    return {**new_card.__dict__, "category_name": category.name}


@app.get("/api/cards", response_model=List[schemas.CardResponse], summary="Get all cards")
async def get_cards(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cards = db.query(models.Card).all()
    
    accessible_cards = []
    for card in cards:
        if has_access_to_card(current_user, card):
            category = db.query(models.Category).filter(models.Category.id == card.category_id).first()
            accessible_cards.append({
                **card.__dict__,
                "category_name": category.name if category else None
            })
    
    return accessible_cards


@app.get("/api/cards/{card_id}", response_model=schemas.CardResponse, summary="Get card by ID")
async def get_card(
    card_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карточка не найдена")
    
    if not has_access_to_card(current_user, card):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к этой карточке"
        )
    
    visit = models.CardVisit(user_id=current_user.id, card_id=card_id)
    db.add(visit)
    db.commit()
    
    category = db.query(models.Category).filter(models.Category.id == card.category_id).first()
    
    return {**card.__dict__, "category_name": category.name if category else None}


@app.put("/api/cards/{card_id}", response_model=schemas.CardResponse, summary="Update card")
async def update_card(
    card_id: int,
    card_update: schemas.CardUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_edit_cards(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования карточек"
        )
    
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карточка не найдена")

    update_data = card_update.dict(exclude_unset=True)
    details = describe_card_changes(card, update_data, db)
    for field, value in update_data.items():
        setattr(card, field, value)

    if update_data:
        log_activity(db, current_user.id, "update", "card", card.id, card.title, details)

    db.commit()
    db.refresh(card)

    category = db.query(models.Category).filter(models.Category.id == card.category_id).first()

    return {**card.__dict__, "category_name": category.name if category else None}


@app.delete("/api/cards/{card_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete card")
async def delete_card(
    card_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_delete_cards(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления карточек"
        )

    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карточка не найдена")

    log_activity(db, current_user.id, "delete", "card", card.id, card.title)
    db.delete(card)
    db.commit()
    return None


@app.get("/api/cards/{card_id}/history", response_model=List[schemas.ActivityLogResponse], summary="Get card edit history")
async def get_card_history(
    card_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    card = db.query(models.Card).filter(models.Card.id == card_id).first()
    if not card:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Карточка не найдена")

    if not can_view_card_activity(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для просмотра истории карточки"
        )

    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.entity_type == "card",
        models.ActivityLog.entity_id == card_id
    ).order_by(models.ActivityLog.created_at.desc()).limit(100).all()

    result = []
    for log in logs:
        actor = db.query(models.User).filter(models.User.id == log.user_id).first()
        result.append({
            "id": log.id,
            "user_id": log.user_id,
            "user_login": actor.login if actor else None,
            "user_name": f"{actor.last_name} {actor.first_name}" if actor else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "entity_title": log.entity_title,
            "details": log.details,
            "created_at": log.created_at,
        })
    return result


# ============ VISIT HISTORY ENDPOINTS ============

@app.get("/api/visits/categories", response_model=List[schemas.VisitResponse], summary="Get category visit history")
async def get_category_visits(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    visits = db.query(models.CategoryVisit).filter(
        models.CategoryVisit.user_id == current_user.id
    ).order_by(models.CategoryVisit.visited_at.desc()).limit(50).all()
    
    result = []
    for visit in visits:
        category = db.query(models.Category).filter(models.Category.id == visit.category_id).first()
        result.append({
            "id": visit.id,
            "item_id": visit.category_id,
            "item_name": category.name if category else "Unknown",
            "visited_at": visit.visited_at
        })
    return result


@app.get("/api/visits/cards", response_model=List[schemas.VisitResponse], summary="Get card visit history")
async def get_card_visits(
    user_id: Optional[int] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get card visit history.
    - If user_id is not provided, returns current user's history
    - If user_id is provided, returns that user's history (only for users with can_edit_any_user permission)
    """
    # Check if requesting another user's history
    if user_id is not None and user_id != current_user.id:
        if not can_edit_any_user(current_user.position_ref):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Недостаточно прав для просмотра истории других пользователей"
            )
        target_user = db.query(models.User).filter(models.User.id == user_id).first()
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пользователь не найден"
            )
        visits_user_id = user_id
    else:
        visits_user_id = current_user.id
    
    visits = db.query(models.CardVisit).filter(
        models.CardVisit.user_id == visits_user_id
    ).order_by(models.CardVisit.visited_at.desc()).limit(50).all()
    
    result = []
    for visit in visits:
        card = db.query(models.Card).filter(models.Card.id == visit.card_id).first()
        result.append({
            "id": visit.id,
            "item_id": visit.card_id,
            "item_name": card.title if card else "Unknown",
            "visited_at": visit.visited_at
        })
    return result


# ============ ACTIVITY LOG ENDPOINTS ============

@app.get("/api/activity/cards", response_model=List[schemas.ActivityLogResponse], summary="Get a user's card create/edit/delete activity")
async def get_card_activity(
    user_id: Optional[int] = None,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get a user's card activity log (create/update/delete).
    Only visible to admin/председатель/заместители председателя — a narrower
    audience than can_edit_any_user, on purpose (this is an oversight tool, not
    a general user-management permission).
    - If user_id is not provided, returns current user's activity
    - If user_id is provided, returns that user's activity
    """
    if not can_view_card_activity(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для просмотра активности по карточкам"
        )

    if user_id is not None:
        target_user = db.query(models.User).filter(models.User.id == user_id).first()
        if not target_user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Пользователь не найден"
            )
        activity_user_id = user_id
    else:
        activity_user_id = current_user.id

    logs = db.query(models.ActivityLog).filter(
        models.ActivityLog.user_id == activity_user_id,
        models.ActivityLog.entity_type == "card"
    ).order_by(models.ActivityLog.created_at.desc()).limit(50).all()

    actor = db.query(models.User).filter(models.User.id == activity_user_id).first()
    return [
        {
            "id": log.id,
            "user_id": log.user_id,
            "user_login": actor.login if actor else None,
            "user_name": f"{actor.last_name} {actor.first_name}" if actor else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "entity_title": log.entity_title,
            "details": log.details,
            "created_at": log.created_at,
        }
        for log in logs
    ]


# ============ DEPARTMENT ENDPOINTS ============

@app.post("/api/departments", response_model=schemas.DepartmentResponse, status_code=status.HTTP_201_CREATED, summary="Create department")
async def create_department(
    department: schemas.DepartmentCreate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new department.
    Available to: admin, председатель, заместитель председателя, председатель студсовета, секретарь
    """
    if not can_manage_departments(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для создания отделов"
        )
    
    existing = db.query(models.Department).filter(models.Department.name == department.name).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Отдел с таким названием уже существует"
        )
    
    new_department = models.Department(name=department.name, description=department.description)
    db.add(new_department)
    db.commit()
    db.refresh(new_department)
    return new_department


@app.get("/api/departments", response_model=List[schemas.DepartmentResponse], summary="Get all departments")
async def get_departments(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all departments - available to all authenticated users"""
    return db.query(models.Department).all()


@app.get("/api/departments/{department_id}", response_model=schemas.DepartmentResponse, summary="Get department by ID")
async def get_department(
    department_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    department = db.query(models.Department).filter(models.Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отдел не найден")
    return department


@app.put("/api/departments/{department_id}", response_model=schemas.DepartmentResponse, summary="Update department")
async def update_department(
    department_id: int,
    department_update: schemas.DepartmentUpdate,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_manage_departments(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования отделов"
        )
    
    department = db.query(models.Department).filter(models.Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отдел не найден")

    update_data = department_update.dict(exclude_unset=True)
    if "name" in update_data and update_data["name"] != department.name:
        existing = db.query(models.Department).filter(models.Department.name == update_data["name"]).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Отдел с таким названием уже существует"
            )

    for field, value in update_data.items():
        setattr(department, field, value)
    
    db.commit()
    db.refresh(department)
    return department


@app.delete("/api/departments/{department_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete department")
async def delete_department(
    department_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_manage_departments(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления отделов"
        )
    
    department = db.query(models.Department).filter(models.Department.id == department_id).first()
    if not department:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отдел не найден")
    
    db.delete(department)
    db.commit()
    return None


@app.get("/")
async def root():
    return {
        "message": "Student Organization Management System API",
        "version": "3.0.0",
        "docs": "/docs"
    }
