from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    hierarchy_level = Column(Integer, default=6)
    can_register_users = Column(Boolean, default=False)
    can_edit_categories = Column(Boolean, default=False)
    can_delete_categories = Column(Boolean, default=False)
    can_edit_cards = Column(Boolean, default=False)
    can_delete_cards = Column(Boolean, default=False)
    can_edit_any_user = Column(Boolean, default=False)
    can_manage_positions = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    users = relationship("User", back_populates="position_ref")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    middle_name = Column(String)
    birth_date = Column(DateTime)
    
    course = Column(String)
    group = Column(String)
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)
    
    telegram = Column(String)
    
    is_active = Column(Boolean, default=True)
    is_deactivated = Column(Boolean, default=False)
    last_login = Column(DateTime)
    password_changed_at = Column(DateTime)

    position_ref = relationship("Position", back_populates="users")
    visited_categories = relationship("CategoryVisit", back_populates="user", cascade="all, delete-orphan")
    visited_cards = relationship("CardVisit", back_populates="user", cascade="all, delete-orphan")
    departments = relationship("UserDepartment", back_populates="user", cascade="all, delete-orphan")


class UserDepartment(Base):
    __tablename__ = "user_departments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="departments")
    department = relationship("Department", back_populates="users")


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    users = relationship("UserDepartment", back_populates="department")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    cards = relationship("Card", back_populates="category", cascade="all, delete-orphan")
    visits = relationship("CategoryVisit", back_populates="category", cascade="all, delete-orphan")


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    access_positions = Column(String)
    access_logins = Column(String)
    
    category = relationship("Category", back_populates="cards")
    visits = relationship("CardVisit", back_populates="card", cascade="all, delete-orphan")


class CategoryVisit(Base):
    __tablename__ = "category_visits"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    visited_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="visited_categories")
    category = relationship("Category", back_populates="visits")


class CardVisit(Base):
    __tablename__ = "card_visits"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=False)
    visited_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="visited_cards")
    card = relationship("Card", back_populates="visits")


class ActivityLog(Base):
    """Audit trail of create/update/delete actions performed by users.

    Deliberately not linked via a cascading relationship from Card/Category,
    so entries survive deletion of the entity they describe (entity_id is a
    plain column, not an ORM relationship) — the whole point is to keep a
    record of "user X deleted card Y" after Y is gone.
    """
    __tablename__ = "activity_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String, nullable=False)          # "create" | "update" | "delete"
    entity_type = Column(String, nullable=False)      # "card"
    entity_id = Column(Integer)
    entity_title = Column(String)                     # snapshot, survives entity edits/deletion
    details = Column(Text)                            # human-readable summary of what changed
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User")
