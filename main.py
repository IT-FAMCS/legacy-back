from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import models
import schemas

from database import engine, get_db, SessionLocal

# ============ CONFIGURATION ============
SECRET_KEY = "your-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30

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

# ============ CORS CONFIGURATION ============
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
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


def can_edit_categories(position: models.Position) -> bool:
    return position.can_edit_categories


def can_delete_categories(position: models.Position) -> bool:
    return position.can_delete_categories


def can_edit_cards(position: models.Position) -> bool:
    return position.can_edit_cards


def can_delete_cards(position: models.Position) -> bool:
    return position.can_delete_cards


def can_edit_any_user(position: models.Position) -> bool:
    return position.can_edit_any_user


def can_manage_departments(position: models.Position) -> bool:
    """Check if position can manage departments"""
    return position.name in ["admin", "председатель", "заместитель председателя", "председатель студсовета", "секретарь"]


def has_higher_authority(manager_position: models.Position, target_position: models.Position) -> bool:
    """Check if manager has higher authority (lower level = higher authority)"""
    return manager_position.hierarchy_level < target_position.hierarchy_level


def has_access_to_card(user: models.User, card: models.Card) -> bool:
    # Admin has access to all cards
    if user.position_ref.name == "admin":
        return True
    
    if not card.access_positions and not card.access_logins:
        return True
    
    if card.access_positions:
        allowed_positions = [p.strip() for p in card.access_positions.split(",")]
        if user.position_ref.name in allowed_positions:
            return True
    
    if card.access_logins:
        allowed_logins = [l.strip() for l in card.access_logins.split(",")]
        if user.login in allowed_logins:
            return True
    
    return False


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
    if not current_user.position_ref.can_manage_positions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для редактирования должностей"
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
    
    existing_user = db.query(models.User).filter(models.User.login == user_data.login).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким логином уже существует"
        )
    
    # Find position by name
    position = db.query(models.Position).filter(models.Position.name == user_data.position).first()
    if not position:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Должность '{user_data.position}' не найдена"
        )
    
    # Check if current user can assign this position
    if not has_higher_authority(current_user.position_ref, position):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для назначения этой должности"
        )
    
    new_user = models.User(
        login=user_data.login,
        password_hash=hash_password(user_data.password),
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        middle_name=user_data.middle_name,
        birth_date=user_data.birth_date,
        course=user_data.course,
        group=user_data.group,
        position_id=position.id,
        department=user_data.department,
        telegram=user_data.telegram,
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return {
        **new_user.__dict__,
        "position_name": position.name
    }


@app.get("/api/users", response_model=List[schemas.UserResponse], summary="Get users list")
async def get_users(
    department: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Get users list - available to all authenticated users"""
    query = db.query(models.User).join(models.Position)
    
    if department:
        query = query.filter(models.User.department == department)
    
    users = query.all()
    result = []
    for user in users:
        result.append({
            **user.__dict__,
            "position_name": user.position_ref.name
        })
    return result


@app.get("/api/user/me", response_model=schemas.UserResponse, summary="Get current user info")
async def get_current_user_info(
    current_user: models.User = Depends(get_current_user)
):
    return {
        **current_user.__dict__,
        "position_name": current_user.position_ref.name
    }


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
            if new_position and not has_higher_authority(current_user.position_ref, new_position):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Недостаточно прав для изменения этой должности"
                )
            target_user.position_id = new_position.id
        
        update_data = user_update.dict(exclude_unset=True, exclude={"position"})
        for field, value in update_data.items():
            if value is not None:
                setattr(target_user, field, value)
        db.commit()
        db.refresh(target_user)
        return {**target_user.__dict__, "position_name": target_user.position_ref.name}
    
    # No permissions
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Недостаточно прав для редактирования пользователей"
    )


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
    db.commit()
    db.refresh(target_user)
    return {**target_user.__dict__, "position_name": target_user.position_ref.name}


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


@app.delete("/api/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete category")
async def delete_category(
    category_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not can_delete_categories(current_user.position_ref):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления категорий"
        )
    
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Категория не найдена")
    
    db.delete(category)
    db.commit()
    return None


# ============ CARD ENDPOINTS ============

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
    for field, value in update_data.items():
        setattr(card, field, value)
    
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
    
    db.delete(card)
    db.commit()
    return None


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
    department_update: schemas.DepartmentCreate,
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
