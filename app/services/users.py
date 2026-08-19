from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.wallet import TokenDirection, TokenLedger, TokenSource
from app.services.wallet import WELCOME_BONUS


class UsernameTakenError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class AccountDisabledError(ValueError):
    pass


class AdminError(ValueError):
    pass


def get_user_by_id(db: Session, user_id: int) -> User | None:
    try:
        return db.get(User, user_id)
    except Exception:
        db.rollback()
        row = get_user_row(db, user_id)
        if row is None:
            return None
        return _user_from_row(row)


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(
        select(User).where(func.lower(User.username) == username.lower())
    )


def create_user(db: Session, username: str, password: str) -> User:
    """Create a user with 100 welcome tokens in the same INSERT/commit.

    Balance is set on the row itself so a wallet lock or ledger hiccup
    cannot leave a successful signup at 0 tokens.
    """
    if get_user_by_username(db, username):
        raise UsernameTakenError("Username already taken.")

    user = User(
        username=username,
        hashed_password=hash_password(password),
        token_balance=WELCOME_BONUS,
        is_disabled=False,
    )
    db.add(user)
    try:
        db.flush()
        db.add(
            TokenLedger(
                user_id=int(user.id),
                direction=TokenDirection.CREDIT,
                amount=WELCOME_BONUS,
                source=TokenSource.WELCOME,
                reason="Welcome bonus",
                reference=f"welcome:{user.id}",
                balance_after=WELCOME_BONUS,
            )
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UsernameTakenError("Username already taken.") from exc
    db.refresh(user)
    if int(user.token_balance or 0) != WELCOME_BONUS:
        user.token_balance = WELCOME_BONUS
        db.commit()
        db.refresh(user)
    return user


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
    if user is None or getattr(user, "id", None) is None:
        return False
    return grant_welcome_if_missing(db, int(user.id))


def grant_welcome_if_missing(db: Session, user_id: int) -> bool:
    """Idempotent +100 welcome credit via UPDATE + ledger INSERT. No row lock."""
    if user_id <= 0:
        return False
    try:
        already = db.execute(
            text(
                "SELECT id FROM token_ledger "
                "WHERE user_id = :uid AND source IN ('welcome', 'WELCOME') LIMIT 1"
            ),
            {"uid": user_id},
        ).first()
    except Exception:
        db.rollback()
        return False
    if already is not None:
        return False

    row = db.execute(
        text("SELECT id, token_balance FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).mappings().first()
    if row is None:
        return False

    current = int(row.get("token_balance") or 0)
    new_balance = current + WELCOME_BONUS
    db.execute(
        text("UPDATE users SET token_balance = :balance WHERE id = :uid"),
        {"balance": new_balance, "uid": user_id},
    )
    db.add(
        TokenLedger(
            user_id=user_id,
            direction=TokenDirection.CREDIT,
            amount=WELCOME_BONUS,
            source=TokenSource.WELCOME,
            reason="Welcome bonus",
            reference=f"welcome:{user_id}",
            balance_after=new_balance,
        )
    )
    db.commit()
    cached = db.get(User, user_id)
    if cached is not None:
        cached.token_balance = new_balance
    return True


def grant_missing_welcome_bonuses(db: Session) -> int:
    """Backfill the 100-token welcome bonus for every account that never got it."""
    try:
        rows = db.execute(
            text(
                "SELECT u.id AS id, COALESCE(u.token_balance, 0) AS token_balance "
                "FROM users u "
                "WHERE NOT EXISTS ("
                "  SELECT 1 FROM token_ledger t "
                "  WHERE t.user_id = u.id AND t.source IN ('welcome', 'WELCOME')"
                ")"
            )
        ).mappings().all()
    except Exception:
        db.rollback()
        return 0

    granted = 0
    for row in rows:
        uid = int(row["id"])
        current = int(row.get("token_balance") or 0)
        new_balance = current + WELCOME_BONUS
        db.execute(
            text("UPDATE users SET token_balance = :balance WHERE id = :uid"),
            {"balance": new_balance, "uid": uid},
        )
        db.add(
            TokenLedger(
                user_id=uid,
                direction=TokenDirection.CREDIT,
                amount=WELCOME_BONUS,
                source=TokenSource.WELCOME,
                reason="Welcome bonus",
                reference=f"welcome:{uid}",
                balance_after=new_balance,
            )
        )
        granted += 1
    if granted:
        db.commit()
    return granted


def _users_columns(db: Session) -> set[str]:
    try:
        bind = db.get_bind()
        return {c["name"] for c in inspect(bind).get_columns("users")}
    except Exception:
        return {"id", "username", "token_balance", "created_at"}


def list_user_rows(db: Session) -> list[dict[str, Any]]:
    """Return every row in users. Column-aware so older DBs still list accounts."""
    cols = _users_columns(db)
    if "id" not in cols or "username" not in cols:
        return []

    select_parts = ["id", "username"]
    if "token_balance" in cols:
        select_parts.append("token_balance")
    else:
        select_parts.append("0 AS token_balance")
    if "is_disabled" in cols:
        select_parts.append("is_disabled")
    else:
        select_parts.append("0 AS is_disabled")
    if "created_at" in cols:
        select_parts.append("created_at")
    else:
        select_parts.append("NULL AS created_at")

    sql = (
        "SELECT "
        + ", ".join(select_parts)
        + " FROM users ORDER BY lower(username), id"
    )
    try:
        rows = db.execute(text(sql)).mappings().all()
    except Exception:
        db.rollback()
        rows = db.execute(
            text("SELECT id, username FROM users ORDER BY id")
        ).mappings().all()

    out: list[dict[str, Any]] = []
    for row in rows:
        created = row.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                created = None
        out.append(
            {
                "id": int(row["id"]),
                "username": str(row["username"]),
                "token_balance": int(row.get("token_balance") or 0),
                "is_disabled": bool(row.get("is_disabled")),
                "created_at": created,
            }
        )
    return out


def list_users(db: Session) -> list[User]:
    return [_user_from_row(row) for row in list_user_rows(db)]


def get_user_row(db: Session, user_id: int) -> dict[str, Any] | None:
    for row in list_user_rows(db):
        if int(row["id"]) == int(user_id):
            return row
    return None


def _user_from_row(row: dict[str, Any]) -> User:
    user = User(
        username=str(row["username"]),
        hashed_password="",
        token_balance=int(row.get("token_balance") or 0),
        is_disabled=bool(row.get("is_disabled")),
    )
    user.id = int(row["id"])
    user.created_at = row.get("created_at")
    return user


def rename_user(db: Session, user: User, username: str) -> User:
    cleaned = (username or "").strip()
    if not cleaned:
        raise UsernameTakenError("Username already taken.")
    updated = rename_user_by_id(db, int(user.id), cleaned)
    user.username = updated["username"]
    return get_user_by_id(db, int(user.id)) or _user_from_row(updated)


def rename_user_by_id(db: Session, user_id: int, username: str) -> dict[str, Any]:
    """Rename any account, including justinv. Raw UPDATE so the ORM cannot skip self."""
    cleaned = (username or "").strip()
    if not cleaned:
        raise UsernameTakenError("Username already taken.")

    existing = db.execute(
        text(
            "SELECT id FROM users WHERE lower(username) = lower(:username) AND id != :uid"
        ),
        {"username": cleaned, "uid": user_id},
    ).first()
    if existing is not None:
        raise UsernameTakenError("Username already taken.")

    try:
        result = db.execute(
            text("UPDATE users SET username = :username WHERE id = :uid"),
            {"username": cleaned, "uid": user_id},
        )
        if result.rowcount == 0:
            raise AdminError("User not found.")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise UsernameTakenError("Username already taken.") from exc

    cached = db.get(User, user_id)
    if cached is not None:
        cached.username = cleaned

    row = get_user_row(db, user_id)
    if row is None:
        raise AdminError("User not found.")
    return row


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
