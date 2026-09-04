from __future__ import annotations

import time
from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
    return kwargs


settings = get_settings()
engine = create_engine(settings.sqlalchemy_database_url, **_engine_kwargs(settings.sqlalchemy_database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_user_columns() -> None:
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    statements: list[str] = []
    if "token_balance" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN token_balance INTEGER NOT NULL DEFAULT 0"
        )
    if "is_disabled" not in columns:
        # PostgreSQL rejects DEFAULT 0 for boolean; SQLite stores boolean as 0/1.
        if engine.dialect.name == "postgresql":
            statements.append(
                "ALTER TABLE users ADD COLUMN is_disabled BOOLEAN NOT NULL DEFAULT false"
            )
        else:
            statements.append(
                "ALTER TABLE users ADD COLUMN is_disabled BOOLEAN NOT NULL DEFAULT 0"
            )
    string_cols = {
        "first_name": "VARCHAR(50)",
        "last_name": "VARCHAR(50)",
        "email": "VARCHAR(254)",
        "postal_code": "VARCHAR(16)",
        "tos_version": "VARCHAR(16)",
        "verification_token_hash": "VARCHAR(64)",
        "signup_profile": "TEXT",
    }
    for name, ddl in string_cols.items():
        if name not in columns:
            statements.append(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    if "email_verified_at" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN email_verified_at TIMESTAMP")
    if "verification_expires_at" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN verification_expires_at TIMESTAMP")
    if "is_verified" not in columns:
        # Existing accounts stay active; new signups set this to false in code.
        if engine.dialect.name == "postgresql":
            statements.append(
                "ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT true"
            )
        else:
            statements.append(
                "ALTER TABLE users ADD COLUMN is_verified BOOLEAN NOT NULL DEFAULT 1"
            )
    if "honor_tally" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN honor_tally INTEGER NOT NULL DEFAULT 0"
        )
    if "honor_skins" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN honor_skins INTEGER NOT NULL DEFAULT 0"
        )
    if "honor_birdies" not in columns:
        statements.append(
            "ALTER TABLE users ADD COLUMN honor_birdies INTEGER NOT NULL DEFAULT 0"
        )
    if "honor_tags" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN honor_tags JSON")
    if "honor_updated_at" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN honor_updated_at TIMESTAMP")
    if "tos_accepted_at" not in columns:
        statements.append("ALTER TABLE users ADD COLUMN tos_accepted_at TIMESTAMP")
    if not statements:
        _ensure_email_unique_index()
        return
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))
    _ensure_email_unique_index()


def _ensure_chat_tables() -> None:
    """Create chat tables if this database predates the chat routes.

    ``create_all`` already adds missing tables and leaves existing ones
    alone. This second pass is explicit so Render logs show whether
    ``chat_threads`` / ``chat_members`` / ``chat_messages`` exist.
    """
    from app.models.chat import ChatMember, ChatMessage, ChatThread  # noqa: F401

    needed = ("chat_threads", "chat_members", "chat_messages")
    tables = [Base.metadata.tables[name] for name in needed if name in Base.metadata.tables]
    if tables:
        Base.metadata.create_all(bind=engine, tables=tables)
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing = [name for name in needed if name not in existing]
    if missing:
        raise RuntimeError(f"chat tables missing after create_all: {', '.join(missing)}")
    print("chat tables ready", flush=True)


def _ensure_email_unique_index() -> None:
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower "
                    "ON users (lower(email))"
                )
            )
    except Exception:
        pass


def init_db(*, retries: int = 8) -> None:
    # Import models so metadata is populated before create_all.
    from app import models  # noqa: F401

    last: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_user_columns()
            _ensure_chat_tables()
            print(f"database ready ({engine.dialect.name})", flush=True)
            return
        except Exception as exc:
            last = exc
            if attempt + 1 >= attempts:
                break
            wait = 1.5 * (attempt + 1)
            print(
                f"database not ready (attempt {attempt + 1}/{attempts}): {exc}",
                flush=True,
            )
            time.sleep(wait)
    assert last is not None
    raise last
