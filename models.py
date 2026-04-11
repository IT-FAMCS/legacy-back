from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, Table
from sqlalchemy.orm import relationship
from datetime import datetime
from database import Base


class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    hierarchy_level = Column(Integer, default=6)  # Lower = higher authority
    can_register_users = Column(Boolean, default=False)
    can_edit_categories = Column(Boolean, default=False)
    can_delete_categories = Column(Boolean, default=False)
    can_edit_cards = Column(Boolean, default=False)
    can_delete_cards = Column(Boolean, default=False)
    can_edit_any_user = Column(Boolean, default=False)
    can_manage_positions = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    users = relationship("User", back_populates="position_ref")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    
    # Personal info
    first_name = Column(String, nullable=False)  # Имя
    last_name = Column(String, nullable=False)   # Фамилия
    middle_name = Column(String)                 # Отчество
    birth_date = Column(DateTime)                # Дата рождения
    
    # Academic/Work info
    course = Column(String)                      # Курс
    group = Column(String)                       # Группа
    position_id = Column(Integer, ForeignKey("positions.id"), nullable=False)  # Должность
    department = Column(String)                  # Отдел
    
    # Contact
    telegram = Column(String)                    # Телеграм
    
    # Account status
    is_active = Column(Boolean, default=True)
    is_deactivated = Column(Boolean, default=False)
    
    # Last login
    last_login = Column(DateTime)
    
    # Relationships
    position_ref = relationship("Position", back_populates="users")
    visited_categories = relationship("CategoryVisit", back_populates="user", cascade="all, delete-orphan")
    visited_cards = relationship("CardVisit", back_populates="user", cascade="all, delete-orphan")


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    cards = relationship("Card", back_populates="category", cascade="all, delete-orphan")
    visits = relationship("CategoryVisit", back_populates="category", cascade="all, delete-orphan")


class Card(Base):
    __tablename__ = "cards"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    content = Column(Text)  # Markdown content
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Access control - comma-separated positions or logins
    # If empty, accessible to all
    access_positions = Column(String)  # Comma-separated positions
    access_logins = Column(String)     # Comma-separated logins
    
    # Relationships
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


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
