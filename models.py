from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, CheckConstraint, Numeric
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
    can_comment_cards = Column(Boolean, default=False)
    is_kaznachey = Column(Boolean, nullable=False, default=False, server_default='0')
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
    phone = Column(String(40))
    education_format = Column(String(16))
    
    is_active = Column(Boolean, default=True)
    is_deactivated = Column(Boolean, default=False)
    last_login = Column(DateTime)          # last time the user authenticated (typed a password)
    last_seen_at = Column(DateTime)        # last time the app was actually open for this user
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
    trainee_access = Column(Boolean, nullable=False, default=False, server_default="0")

    category = relationship("Category", back_populates="cards")
    visits = relationship("CardVisit", back_populates="card", cascade="all, delete-orphan")
    comments = relationship("CardComment", back_populates="card", cascade="all, delete-orphan")


class TraineeCode(Base):
    """Singleton (id=1), shared by all workers; version revokes guest sessions."""
    __tablename__ = "trainee_code"

    id = Column(Integer, primary_key=True)
    code = Column(String, nullable=False)
    version = Column(String, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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


class CardComment(Base):
    """Margin comment left on a quoted excerpt of a card's content.

    Unlike ActivityLog, this DOES cascade with its Card (see Card.comments) —
    a comment anchored to a specific passage has no purpose once the card
    itself is gone, unlike an audit-trail entry.
    """
    __tablename__ = "card_comments"

    id = Column(Integer, primary_key=True, index=True)
    card_id = Column(Integer, ForeignKey("cards.id"), nullable=False)
    author_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    quoted_text = Column(Text, nullable=False)
    body = Column(Text, nullable=False)
    resolved = Column(Boolean, default=False)
    resolved_by_id = Column(Integer, ForeignKey("users.id"))
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    card = relationship("Card", back_populates="comments")
    author = relationship("User", foreign_keys=[author_id])
    resolved_by = relationship("User", foreign_keys=[resolved_by_id])


class EquipmentLocation(Base):
    __tablename__ = "equipment_locations"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), nullable=False)
    name_key = Column(String(120), unique=True, nullable=False)


class EquipmentItem(Base):
    __tablename__ = "equipment_items"
    __table_args__ = (CheckConstraint(
        "(holder_user_id IS NOT NULL AND location_id IS NULL AND taken_at IS NOT NULL) OR "
        "(holder_user_id IS NULL AND location_id IS NOT NULL AND taken_at IS NULL)",
        name="equipment_exactly_one_holder"),)
    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default="")
    holder_user_id = Column(Integer, ForeignKey("users.id"))
    location_id = Column(Integer, ForeignKey("equipment_locations.id"))
    taken_at = Column(DateTime)
    version = Column(Integer, nullable=False, default=1)
    deleted_at = Column(DateTime)
    holder_user = relationship("User")
    location = relationship("EquipmentLocation")


class EquipmentHistory(Base):
    __tablename__ = "equipment_history"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("equipment_items.id"), nullable=False, index=True)
    action = Column(String(16), nullable=False)
    actor_name = Column(String, nullable=False)
    actor_login = Column(String, nullable=False)
    holder_name = Column(String, nullable=False)
    holder_login = Column(String)
    taken_at = Column(DateTime)
    recorded_at = Column(DateTime, nullable=False)


class MemberWarning(Base):
    __tablename__ = "member_warnings"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reason = Column(Text, nullable=False)
    issued_at = Column(DateTime, nullable=False)
    issued_by_login = Column(String, nullable=False)
    issued_by_name = Column(String, nullable=False)
    removed_at = Column(DateTime)
    removed_by_login = Column(String)
    removed_by_name = Column(String)
    removal_reason = Column(Text)

# Shared tasks: links reference existing cards, never the account Department directory.
class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=False, default="")
    deadline = Column(String(10))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    creator_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    event_card_id = Column(Integer, ForeignKey("cards.id"), index=True)
    completed = Column(Boolean, nullable=False, default=False)
    archived = Column(Boolean, nullable=False, default=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": version}
    creator = relationship("User", foreign_keys=[creator_id])
    event_card = relationship("Card", foreign_keys=[event_card_id])
    department_links = relationship("TaskDepartment", cascade="all, delete-orphan", lazy="selectin")
    assignee_links = relationship("TaskAssignee", cascade="all, delete-orphan", lazy="selectin")


class TaskIdSequence(Base):
    __tablename__ = 'task_id_sequence'
    id = Column(Integer, primary_key=True)
    last_deleted_id = Column(Integer, nullable=False)


class TaskNotificationMute(Base):
    __tablename__ = 'task_notification_mutes'
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    # Zero mutes all tasks for this user. Positive scopes identify individual tasks.
    scope = Column(Integer, primary_key=True)


class TaskDepartment(Base):
    __tablename__ = "task_departments"
    task_id = Column(Integer, ForeignKey("tasks.id"), primary_key=True)
    card_id = Column(Integer, ForeignKey("cards.id"), primary_key=True, index=True)
    card = relationship("Card")


class TaskAssignee(Base):
    __tablename__ = "task_assignees"
    task_id = Column(Integer, ForeignKey("tasks.id"), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    user = relationship("User")


class TaskHistory(Base):
    __tablename__ = "task_history"
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    actor_name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    before_json = Column(Text)
    after_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

# Telegram identity is independent from editable contact fields.
class TelegramBinding(Base):
    __tablename__ = 'telegram_bindings'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False)
    telegram_user_id = Column(Integer, unique=True, nullable=True)
    chat_id = Column(Integer)
    username = Column(String(64))
    display_name = Column(String(200))
    generation = Column(String(64), nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    blocked = Column(Boolean, nullable=False, default=False)
    linked_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    user = relationship('User')


class TelegramLinkChallenge(Base):
    __tablename__ = 'telegram_link_challenges'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    consumed_at = Column(DateTime)
    telegram_user_id = Column(Integer)
    chat_id = Column(Integer)
    username = Column(String(64))
    display_name = Column(String(200))


class TelegramRateLimit(Base):
    __tablename__ = 'telegram_rate_limits'
    key = Column(String(160), primary_key=True)
    window = Column(DateTime, nullable=False)
    count = Column(Integer, nullable=False)


class TelegramRequestReceipt(Base):
    __tablename__ = 'telegram_request_receipts'
    key = Column(String(64), primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    response_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TelegramNotification(Base):
    __tablename__ = 'telegram_notifications'
    id = Column(Integer, primary_key=True)
    event_key = Column(String(200), unique=True, nullable=False)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    binding_id = Column(Integer, ForeignKey('telegram_bindings.id'), nullable=False)
    generation = Column(String(64), nullable=False)
    kind = Column(String(20), nullable=False)
    deadline = Column(String(10))
    state = Column(String(20), nullable=False, default='pending', index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    lease_token = Column(String(64))
    lease_until = Column(DateTime)
    last_error = Column(String(40))

class ResponsibilityBoard(Base):
    __tablename__ = 'responsibility_boards'
    card_id = Column(Integer, ForeignKey('cards.id'), primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __mapper_args__ = {'version_id_col': version}


class ResponsibilityZone(Base):
    __tablename__ = 'responsibility_zones'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, ForeignKey('cards.id'), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    people_required = Column(Integer, nullable=False, default=1)
    lead_user_id = Column(Integer, ForeignKey('users.id'))
    version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    __mapper_args__ = {'version_id_col': version}
    lead = relationship('User', foreign_keys=[lead_user_id])
    helper_links = relationship('ResponsibilityHelper', cascade='all, delete-orphan', lazy='selectin')


class BudgetRequest(Base):
    __tablename__ = 'budget_requests'
    id = Column(Integer, primary_key=True)
    request_type = Column(String(24), nullable=False, index=True)
    applicant_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    applicant_name = Column(String(240), nullable=False)
    department_snapshot = Column(Text, nullable=False, default='[]')
    description = Column(Text, nullable=False)
    submitted_amount = Column(Numeric(12, 2), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    payout_details = Column(Text)
    telegram_file_id = Column(Text)
    status = Column(String(20), nullable=False, default='pending', index=True)
    decision = Column(String(20))
    account = Column(String(12))
    processed_by = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    processed_at = Column(DateTime)
    applicant = relationship('User', foreign_keys=[applicant_id])
    processor = relationship('User', foreign_keys=[processed_by])
    amount_changes = relationship('BudgetRequestAmountChange', cascade='all, delete-orphan', order_by='BudgetRequestAmountChange.id')


class BudgetRequestAmountChange(Base):
    __tablename__ = 'budget_request_amount_changes'
    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey('budget_requests.id'), nullable=False, index=True)
    old_amount = Column(Numeric(12, 2), nullable=False)
    new_amount = Column(Numeric(12, 2), nullable=False)
    editor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    editor = relationship('User')


class BudgetEntry(Base):
    __tablename__ = 'budget_entries'
    id = Column(Integer, primary_key=True)
    direction = Column(String(12), nullable=False)
    account = Column(String(12), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    description = Column(Text, nullable=False)
    operation_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    request_id = Column(Integer, ForeignKey('budget_requests.id'), unique=True)
    deleted_at = Column(DateTime)
    deleted_by = Column(Integer, ForeignKey('users.id'))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    creator = relationship('User', foreign_keys=[created_by])
    editor = relationship('User', foreign_keys=[updated_by])
    treasurer_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    treasurer = relationship('User', foreign_keys=[treasurer_id])
    request = relationship('BudgetRequest')


class BudgetNotification(Base):
    __tablename__ = 'budget_notifications'
    id = Column(Integer, primary_key=True)
    event_type = Column(String(24), nullable=False)
    request_id = Column(Integer, ForeignKey('budget_requests.id'), nullable=False, index=True)
    recipient_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    binding_id = Column(Integer, ForeignKey('telegram_bindings.id'), nullable=False)
    generation = Column(String(64), nullable=False)
    state = Column(String(16), nullable=False, default='pending', index=True)
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    lease_token = Column(String(64))
    lease_until = Column(DateTime)
    payload = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BudgetNotificationDelivery(Base):
    __tablename__ = 'budget_notification_deliveries'
    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey('budget_requests.id'), nullable=False, index=True)
    binding_id = Column(Integer, ForeignKey('telegram_bindings.id'), nullable=False)
    generation = Column(String(64), nullable=False)
    detail_message_id = Column(Integer)
    photo_message_id = Column(Integer)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (CheckConstraint('request_id > 0'),)


class ResponsibilityHelper(Base):
    __tablename__ = 'responsibility_helpers'
    zone_id = Column(Integer, ForeignKey('responsibility_zones.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    user = relationship('User')


class ResponsibilityHistory(Base):
    __tablename__ = 'responsibility_history'
    id = Column(Integer, primary_key=True)
    card_id = Column(Integer, nullable=False, index=True)
    zone_id = Column(Integer, nullable=False)
    actor_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    actor_name = Column(String, nullable=False)
    action = Column(String, nullable=False)
    before_json = Column(Text)
    after_json = Column(Text)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class BudgetImportBatch(Base):
    __tablename__ = 'budget_import_batches'
    id = Column(String(64), primary_key=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    payload = Column(Text, nullable=False)
    result = Column(Text)
    expires_at = Column(DateTime, nullable=False)


class BudgetImportSource(Base):
    __tablename__ = 'budget_import_sources'
    source_key = Column(String(64), primary_key=True)
    fingerprint = Column(String(64), nullable=False)
    entry_id = Column(Integer, ForeignKey('budget_entries.id'), nullable=False)


class BudgetCustodyAdjustment(Base):
    __tablename__ = 'budget_custody_adjustments'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    account = Column(String(12), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    balance_before = Column(Numeric(12, 2), nullable=False)
    balance_after = Column(Numeric(12, 2), nullable=False)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PersonalDataConsent(Base):
    __tablename__ = 'personal_data_consents'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    scope = Column(String(24), nullable=False)
    version = Column(String(64), nullable=False)
    policy_snapshot = Column(Text, nullable=False)
    source = Column(String(16), nullable=False)
    accepted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    revoked_at = Column(DateTime)


class SavedPayoutDetail(Base):
    __tablename__ = 'saved_payout_details'
    __table_args__ = {'sqlite_autoincrement': True}
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    label = Column(String(60), nullable=False)
    details = Column(String(500), nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
