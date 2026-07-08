from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# ============ POSITION SCHEMAS ============

class PositionCreate(BaseModel):
    """Schema for creating a position"""
    name: str = Field(..., min_length=1, max_length=100)
    hierarchy_level: int = Field(default=6, ge=1, le=10)
    can_register_users: bool = False
    can_edit_categories: bool = False
    can_delete_categories: bool = False
    can_edit_cards: bool = False
    can_delete_cards: bool = False
    can_edit_any_user: bool = False
    can_manage_positions: bool = False


class PositionUpdate(BaseModel):
    """Schema for updating a position"""
    name: Optional[str] = None
    hierarchy_level: Optional[int] = None
    can_register_users: Optional[bool] = None
    can_edit_categories: Optional[bool] = None
    can_delete_categories: Optional[bool] = None
    can_edit_cards: Optional[bool] = None
    can_delete_cards: Optional[bool] = None
    can_edit_any_user: Optional[bool] = None
    can_manage_positions: Optional[bool] = None


class PositionResponse(BaseModel):
    """Schema for position response"""
    id: int
    name: str
    hierarchy_level: int
    can_register_users: bool
    can_edit_categories: bool
    can_delete_categories: bool
    can_edit_cards: bool
    can_delete_cards: bool
    can_edit_any_user: bool
    can_manage_positions: bool
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ DEPARTMENT SCHEMAS ============

class DepartmentCreate(BaseModel):
    """Schema for creating a department"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None


class DepartmentUpdate(BaseModel):
    """Schema for updating a department"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None


class DepartmentResponse(BaseModel):
    """Schema for department response"""
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class UserDepartmentCreate(BaseModel):
    """Schema for assigning a user to a department"""
    department_id: int


# ============ USER SCHEMAS ============

class UserRegister(BaseModel):
    """Schema for user registration (by admin)"""
    login: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    
    # Personal info
    first_name: str = Field(..., min_length=1, max_length=100)  # Имя
    last_name: str = Field(..., min_length=1, max_length=100)   # Фамилия
    middle_name: Optional[str] = None                            # Отчество
    birth_date: Optional[datetime] = None                        # Дата рождения
    
    # Academic/Work info
    course: Optional[str] = None                                 # Курс
    group: Optional[str] = None                                  # Группа
    position: Optional[str] = "участник"                         # Должность
    department_ids: Optional[List[int]] = None                   # Отделы (список ID)
    
    # Contact
    telegram: Optional[str] = None                               # Телеграм


class BulkUserRegister(BaseModel):
    """Schema for bulk user registration"""
    users: List[UserRegister] = Field(..., min_length=1)


class UserLogin(BaseModel):
    """Schema for user login"""
    login: str
    password: str


class UserUpdate(BaseModel):
    """Schema for updating user data"""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    middle_name: Optional[str] = None
    birth_date: Optional[datetime] = None
    course: Optional[str] = None
    group: Optional[str] = None
    position: Optional[str] = None
    department_ids: Optional[List[int]] = None  # Список ID отделов
    telegram: Optional[str] = None
    is_active: Optional[bool] = None
    is_deactivated: Optional[bool] = None


class PasswordChange(BaseModel):
    """Schema for changing user password"""
    password: str = Field(..., min_length=6, description="New password (min 6 characters)")


class OwnPasswordChange(BaseModel):
    """Schema for changing current user's own password"""
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


class DepartmentInfo(BaseModel):
    """Schema for department info in user response"""
    id: int
    name: str
    
    class Config:
        from_attributes = True


class UserResponse(BaseModel):
    """Schema for user response"""
    id: int
    login: str
    first_name: str
    last_name: str
    middle_name: Optional[str] = None
    birth_date: Optional[datetime] = None
    course: Optional[str] = None
    group: Optional[str] = None
    position_id: int
    position_name: Optional[str] = None
    departments: Optional[List[DepartmentInfo]] = None  # Список отделов
    telegram: Optional[str] = None
    is_active: bool
    is_deactivated: bool
    last_login: Optional[datetime] = None
    
    class Config:
        from_attributes = True


class Token(BaseModel):
    """Schema for JWT token response"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


# ============ CATEGORY SCHEMAS ============

class CategoryCreate(BaseModel):
    """Schema for creating a category"""
    name: str = Field(..., min_length=1, max_length=200)
    description: Optional[str] = None


class CategoryUpdate(BaseModel):
    """Schema for updating a category"""
    name: Optional[str] = None
    description: Optional[str] = None


class CategoryResponse(BaseModel):
    """Schema for category response"""
    id: int
    name: str
    description: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


class CategoryWithCards(CategoryResponse):
    """Category with its cards"""
    cards_count: int = 0


# ============ CARD SCHEMAS ============

class CardCreate(BaseModel):
    """Schema for creating a card"""
    title: str = Field(..., min_length=1, max_length=200)
    content: Optional[str] = None
    category_id: int
    access_positions: Optional[str] = None  # Comma-separated positions
    access_logins: Optional[str] = None     # Comma-separated logins


class CardUpdate(BaseModel):
    """Schema for updating a card"""
    title: Optional[str] = None
    content: Optional[str] = None
    category_id: Optional[int] = None
    access_positions: Optional[str] = None
    access_logins: Optional[str] = None


class CardResponse(BaseModel):
    """Schema for card response"""
    id: int
    title: str
    content: Optional[str] = None
    category_id: int
    category_name: Optional[str] = None
    access_positions: Optional[str] = None
    access_logins: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# ============ VISIT SCHEMAS ============

class VisitResponse(BaseModel):
    """Schema for visit history"""
    id: int
    item_id: int
    item_name: str
    visited_at: datetime
    
    class Config:
        from_attributes = True


# ============ ERROR SCHEMAS ============

class ErrorResponse(BaseModel):
    """Schema for error responses"""
    detail: str
    error_code: Optional[str] = None
