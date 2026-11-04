import os

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.db")

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_upgrades() -> None:
    """Add columns introduced after the initial create_all to already-existing
    SQLite databases. create_all() only creates missing tables, it never
    alters existing ones.

    Must be called AFTER Base.metadata.create_all(), and by every entrypoint
    that touches the users table before gunicorn/main.py gets a chance to run
    — seed_db_v2.py runs as a separate process before gunicorn in the
    Dockerfile CMD and queries models.User directly, so it needs this too, not
    just main.py, or it crashes on "no such column" against a pre-migration
    prod DB before the app ever starts (never mind the app's own copy of this
    upgrade running later).

    gunicorn also boots several worker processes that each import main.py
    independently, so this can legitimately race: two callers can both see a
    column missing and both try to ALTER TABLE. SQLite lets only one succeed;
    the loser must not crash over it."""
    if engine.dialect.name != "sqlite":
        return

    users_columns_to_add = {
        "password_changed_at": "DATETIME",
        "last_seen_at": "DATETIME",
    }

    with engine.begin() as conn:
        existing_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()}
        if not existing_columns:
            # users table doesn't exist yet (fresh DB) — create_all() will
            # make it with the current schema already, nothing to patch.
            return
        for column, sql_type in users_columns_to_add.items():
            if column in existing_columns:
                continue
            try:
                conn.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {column} {sql_type}")
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise

    with engine.begin() as conn:
        existing_position_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(positions)").fetchall()}
        if not existing_position_columns:
            # positions table doesn't exist yet (fresh DB) — create_all()
            # already made it with the current schema, nothing to patch.
            return

        if "can_comment_cards" not in existing_position_columns:
            try:
                conn.exec_driver_sql("ALTER TABLE positions ADD COLUMN can_comment_cards BOOLEAN DEFAULT 0")
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            else:
                # One-time default for an EXISTING (pre-upgrade) database only —
                # never re-applied on later runs, unlike main.py's
                # ensure_runtime_permissions(), so an admin's later edit via the
                # Positions page sticks across restarts/deploys. Mirrors the
                # same name-family matching as is_admin_chair_or_deputy() /
                # is_department_lead() in main.py (duplicated here in raw SQL —
                # this module can't import main.py without a circular import).
                conn.exec_driver_sql(
                    """
                    UPDATE positions SET can_comment_cards = 1
                    WHERE lower(trim(name)) IN (
                        'admin', 'админ',
                        'председатель', 'председатель студсовета',
                        'руководитель отдела', 'руководитель отдела/направления',
                        'заместитель руководителя отдела'
                    )
                    OR lower(trim(name)) LIKE 'заместитель председателя%'
                    OR lower(trim(name)) LIKE 'зам. председателя%'
                    OR lower(trim(name)) LIKE 'заместитель руководителя%'
                    """
                )

    with engine.begin() as conn:
        card_columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(cards)").fetchall()}
        if card_columns and "trainee_access" not in card_columns:
            try:
                conn.exec_driver_sql("ALTER TABLE cards ADD COLUMN trainee_access BOOLEAN NOT NULL DEFAULT 0")
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
