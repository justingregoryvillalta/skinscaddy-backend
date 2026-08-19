from __future__ import annotations

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.wallet import TokenLedger, TokenSource
from app.services.wallet import WELCOME_BONUS, award_tokens


class UsernameTakenError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(
        select(User).where(func.lower(User.username) == username.lower())
    )


def create_user(db: Session, username: str, password: str) -> User:
    if get_user_by_username(db, username):
        raise UsernameTakenError("Username already taken.")

    user = User(
        username=username,
        hashed_password=hash_password(password),
        token_balance=0,
    )
    db.add(user)
    try:
        db.flush()
        award_tokens(
            db,
            user,
            TokenSource.WELCOME,
            amount=WELCOME_BONUS,
            reason="Welcome bonus",
            reference=f"welcome:{user.id}",
            commit=False,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UsernameTakenError("Username already taken.") from exc
    db.refresh(user)
    return user


class AccountDisabledError(ValueError):
    pass


class AdminError(ValueError):
    pass


def authenticate_user(db: Session, username: str, password: str) -> User:
    user = get_user_by_username(db, username)
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError("Invalid username or password.")
    if getattr(user, "is_disabled", False):
        raise AccountDisabledError("This account is disabled.")
    ensure_welcome_bonus(db, user)
    return user


def ensure_welcome_bonus(db: Session, user: User) -> bool:
    """Credit 100 tokens once if this account never received the welcome bonus."""
    if user.id is None:
        return False
    already = db.scalar(
        select(TokenLedger.id).where(
            TokenLedger.user_id == user.id,
            TokenLedger.source == TokenSource.WELCOME,
        )
    )
    if already is not None:
        return False
    award_tokens(
        db,
        user,
        TokenSource.WELCOME,
        amount=WELCOME_BONUS,
        reason="Welcome bonus",
        reference=f"welcome:{user.id}",
    )
    return True


def list_users(db: Session) -> list[User]:
    try:
        return list(db.scalars(select(User).order_by(func.lower(User.username))).all())
    except Exception:
        db.rollback()
        return _list_users_raw(db)


def _list_users_raw(db: Session) -> list[User]:
    """Fallback if a new User column is missing on an older database."""
    cols = set()
    try:
        bind = db.get_bind()
        cols = {c["name"] for c in inspect(bind).get_columns("users")}
    except Exception:
        cols = {"id", "username", "token_balance", "created_at"}
    disabled_sql = "is_disabled" if "is_disabled" in cols else "0 AS is_disabled"
    rows = db.execute(
        text(
            "SELECT id, username, token_balance, created_at, "
            f"{disabled_sql} FROM users ORDER BY lower(username)"
        )
    ).mappings()
    out: list[User] = []
    for row in rows:
        user = User(
            username=str(row["username"]),
            hashed_password="",
            token_balance=int(row.get("token_balance") or 0),
        )
        user.id = int(row["id"])
        user.created_at = row["created_at"]
        user.is_disabled = bool(row.get("is_disabled"))
        out.append(user)
    return out


def rename_user(db: Session, user: User, username: str) -> User:
    cleaned = (username or "").strip()
    if cleaned.lower() == user.username.lower():
        user.username = cleaned
        db.commit()
        db.refresh(user)
        return user
    existing = get_user_by_username(db, cleaned)
    if existing is not None and existing.id != user.id:
        raise UsernameTakenError("Username already taken.")
    user.username = cleaned
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UsernameTakenError("Username already taken.") from exc
    db.refresh(user)
    return user


def set_user_disabled(db: Session, user: User, disabled: bool) -> User:
    user.is_disabled = bool(disabled)
    db.commit()
    db.refresh(user)
    return user


def delete_user(db: Session, user: User) -> None:
    db.delete(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AdminError("Could not delete this user. Flag the account instead.") from exc
