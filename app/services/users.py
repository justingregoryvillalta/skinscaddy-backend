from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.wallet import TokenSource
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

    user = User(username=username, hashed_password=hash_password(password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UsernameTakenError("Username already taken.") from exc
    db.refresh(user)
    award_tokens(
        db,
        user,
        TokenSource.WELCOME,
        amount=WELCOME_BONUS,
        reason="Welcome bonus",
        reference=f"welcome:{user.id}",
    )
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
    return user


def list_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).order_by(func.lower(User.username))).all())


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
