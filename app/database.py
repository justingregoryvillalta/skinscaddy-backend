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
        statements.append(
            "ALTER TABLE users ADD COLUMN is_disabled BOOLEAN NOT NULL DEFAULT 0"
        )
    if not statements:
        return
    with engine.begin() as conn:
        for sql in statements:
            conn.execute(text(sql))


def init_db(*, retries: int = 8) -> None:
    # Import models so metadata is populated before create_all.
    from app import models  # noqa: F401

    last: Exception | None = None
    attempts = max(1, int(retries))
    for attempt in range(attempts):
        try:
            Base.metadata.create_all(bind=engine)
            _ensure_user_columns()
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
