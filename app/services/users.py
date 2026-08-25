from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.user import User
from app.models.wallet import TokenDirection, TokenLedger, TokenSource
from app.services.signup_profile import (
    INTENT_PRACTICE,
    INTENT_SCORE,
    SignupProfileError,
    dump_profile,
    first_skins_topup_target,
    parse_profile,
    validate_answers,
)
from app.services.wallet import WELCOME_BONUS, credit_tokens

ADMIN_LOGIN_USERNAME = "admin"
ADMIN_PASSWORD_SOURCE_USERNAME = "justinv"


class UsernameTakenError(ValueError):
    pass


class EmailTakenError(ValueError):
    pass


class InvalidCredentialsError(ValueError):
    pass


class AccountDisabledError(ValueError):
    pass


class AccountUnverifiedError(ValueError):
    pass


class VerificationError(ValueError):
    pass


class AdminError(ValueError):
    pass


def is_admin_login_username(username: str) -> bool:
    return (username or "").strip().lower() == ADMIN_LOGIN_USERNAME


def is_reserved_username(username: str) -> bool:
    return is_admin_login_username(username)


def is_admin_account(username: str) -> bool:
    """True for the dedicated admin login and the legacy justinv admin."""
    name = (username or "").strip().lower()
    if not name:
        return False
    if name == ADMIN_LOGIN_USERNAME:
        return True
    try:
        configured = (get_settings().ADMIN_USERNAME or ADMIN_PASSWORD_SOURCE_USERNAME)
    except Exception:
        configured = ADMIN_PASSWORD_SOURCE_USERNAME
    configured = str(configured or "").strip().lower()
    return name == configured or name == ADMIN_PASSWORD_SOURCE_USERNAME.lower()


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


def normalize_email(email: str | None) -> str:
    return (email or "").strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    cleaned = normalize_email(email)
    if not cleaned:
        return None
    return db.scalar(select(User).where(func.lower(User.email) == cleaned))


def hash_verification_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def normalize_verify_token(raw_token: str) -> str:
    """Same string we put in ?token= after the client/browser decodes the URL."""
    from urllib.parse import unquote

    token = (raw_token or "").strip().strip("<>\"'")
    for _ in range(2):
        nxt = unquote(token)
        if nxt == token:
            break
        token = nxt.strip()
    while token and token[-1] in ".,);]>":
        token = token[:-1]
    return token.strip()


def issue_verification_token(user: User) -> str:
    # urlsafe so it can sit in an email link; we store sha256 of THIS string.
    raw = secrets.token_urlsafe(32)
    hours = max(24, int(get_settings().VERIFICATION_HOURS or 48))
    user.verification_token_hash = hash_verification_token(raw)
    user.verification_expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
    user.is_verified = False
    print(
        f"verify token issued user={getattr(user, 'username', '?')} "
        f"len={len(raw)} prefix={raw[:4]} hours={hours}",
        flush=True,
    )
    return raw


def create_user(
    db: Session,
    username: str,
    password: str,
    *,
    first_name: str,
    last_name: str,
    email: str,
    postal_code: str,
) -> tuple[User, str]:
    """Create an unverified user with 100 welcome tokens. Returns (user, raw token)."""
    if is_reserved_username(username):
        raise UsernameTakenError("Username already taken.")
    cleaned_email = normalize_email(email)
    release_reusable_identities(db, username=username, email=cleaned_email)
    if get_user_by_username(db, username):
        raise UsernameTakenError("Username already taken.")
    if get_user_by_email(db, cleaned_email):
        raise EmailTakenError("An account with this email already exists.")

    user = User(
        username=username,
        hashed_password=hash_password(password),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        email=cleaned_email,
        postal_code=(postal_code or "").strip().upper(),
        token_balance=WELCOME_BONUS,
        is_disabled=False,
        is_verified=False,
    )
    raw_token = issue_verification_token(user)
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
        if get_user_by_email(db, cleaned_email):
            raise EmailTakenError("An account with this email already exists.") from exc
        raise UsernameTakenError("Username already taken.") from exc
    db.refresh(user)
    if int(user.token_balance or 0) != WELCOME_BONUS:
        user.token_balance = WELCOME_BONUS
        db.commit()
        db.refresh(user)
    return user, raw_token


def verify_user_token(db: Session, raw_token: str) -> User:
    token = normalize_verify_token(raw_token)
    prefix = token[:4] if token else ""
    if len(token) < 8:
        print(f"verify lookup missing/short len={len(token)} prefix={prefix}", flush=True)
        raise VerificationError("This activation link is invalid.")
    digest = hash_verification_token(token)
    user = db.scalar(select(User).where(User.verification_token_hash == digest))
    # Legacy: some rows may have stored the urlsafe token itself, not the hash.
    if user is None:
        user = db.scalar(select(User).where(User.verification_token_hash == token))
    if user is None:
        print(
            f"verify lookup missing len={len(token)} prefix={prefix} hashed=yes",
            flush=True,
        )
        raise VerificationError("This activation link is invalid or has already been used.")
    already = bool(getattr(user, "is_verified", False))
    expires = getattr(user, "verification_expires_at", None)
    expired = False
    if expires is not None and not already:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        expired = expires < datetime.now(timezone.utc)
    print(
        f"verify lookup found user={user.username} already={already} expired={expired} "
        f"len={len(token)} prefix={prefix}",
        flush=True,
    )
    if already:
        return user
    if expired:
        raise VerificationError("This activation link has expired. Request a new one.")
    user.is_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    # Keep the hash so a second click on the same link is SUCCESS, not failure.
    db.commit()
    db.refresh(user)
    print(f"verify success user={user.username}", flush=True)
    return user


def _find_verification_user(
    db: Session, *, email: str | None, username: str | None
) -> User | None:
    """Prefer an unverified match so Send verification resends instead of 404."""
    by_email = get_user_by_email(db, email) if email else None
    by_name = get_user_by_username(db, username) if username else None
    for candidate in (by_email, by_name):
        if candidate is not None and getattr(candidate, "is_verified", True) is False:
            return candidate
    return by_email or by_name


def resend_verification(db: Session, *, email: str | None = None, username: str | None = None) -> tuple[User, str]:
    user = _find_verification_user(db, email=email, username=username)
    if user is None:
        raise InvalidCredentialsError("No account found for that email or username.")
    if getattr(user, "is_verified", True):
        raise VerificationError("This account is already activated.")
    if not (user.email or "").strip():
        raise VerificationError("This account has no email address.")
    raw = issue_verification_token(user)
    db.commit()
    db.refresh(user)
    return user, raw


def authenticate_user(db: Session, username: str, password: str) -> User:
    if is_admin_login_username(username):
        return authenticate_admin(db, password)
    user = get_user_by_username(db, username)
    if user is None or not verify_password(password, user.hashed_password):
        raise InvalidCredentialsError("Invalid username or password.")
    if getattr(user, "is_disabled", False):
        raise AccountDisabledError("This account is disabled.")
    if getattr(user, "is_verified", True) is False:
        raise AccountUnverifiedError(
            "Please verify your email. Check your inbox for the activation link."
        )
    ensure_welcome_bonus(db, user)
    return user


def _create_admin_user(db: Session, hashed_password: str) -> User:
    """Create the dedicated admin login account (no welcome bonus)."""
    user = User(
        username=ADMIN_LOGIN_USERNAME,
        hashed_password=hashed_password,
        token_balance=0,
        is_disabled=False,
        is_verified=True,
    )
    db.add(user)
    try:
        db.flush()
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        existing = get_user_by_username(db, ADMIN_LOGIN_USERNAME)
        if existing is not None:
            return existing
        raise UsernameTakenError("Username already taken.") from exc
    db.refresh(user)
    return user


def authenticate_admin(db: Session, password: str) -> User:
    """Admin portal login: username `admin`, password = current justinv password."""
    source = get_user_by_username(db, ADMIN_PASSWORD_SOURCE_USERNAME)
    admin = get_user_by_username(db, ADMIN_LOGIN_USERNAME)
    source_ok = bool(
        source is not None and verify_password(password, source.hashed_password)
    )
    admin_ok = bool(
        admin is not None and verify_password(password, admin.hashed_password)
    )
    if not source_ok and not admin_ok:
        raise InvalidCredentialsError("Invalid username or password.")

    if admin is None:
        hashed = source.hashed_password if source_ok and source is not None else ""
        if not hashed:
            hashed = hash_password(password)
        admin = _create_admin_user(db, hashed)
    elif source_ok and source is not None and admin.hashed_password != source.hashed_password:
        admin.hashed_password = source.hashed_password
        db.commit()
        db.refresh(admin)

    if getattr(admin, "is_disabled", False):
        raise AccountDisabledError("This account is disabled.")
    return admin


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
        text("SELECT id, token_balance, username FROM users WHERE id = :uid"),
        {"uid": user_id},
    ).mappings().first()
    if row is None:
        return False
    if is_admin_login_username(str(row.get("username") or "")):
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
                "WHERE lower(u.username) != :admin_name "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM token_ledger t "
                "  WHERE t.user_id = u.id AND t.source IN ('welcome', 'WELCOME')"
                ")"
            ),
            {"admin_name": ADMIN_LOGIN_USERNAME},
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
    if "is_verified" in cols:
        select_parts.append("is_verified")
    else:
        select_parts.append("1 AS is_verified")
    for extra in ("first_name", "last_name", "email", "postal_code", "signup_profile"):
        if extra in cols:
            select_parts.append(extra)
        else:
            select_parts.append(f"NULL AS {extra}")
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
                "first_name": (str(row["first_name"]) if row.get("first_name") else None),
                "last_name": (str(row["last_name"]) if row.get("last_name") else None),
                "email": (str(row["email"]) if row.get("email") else None),
                "postal_code": (str(row["postal_code"]) if row.get("postal_code") else None),
                "token_balance": int(row.get("token_balance") or 0),
                "is_disabled": bool(row.get("is_disabled")),
                "is_verified": bool(row["is_verified"]) if row.get("is_verified") is not None else True,
                "signup_profile": row.get("signup_profile"),
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
        first_name=row.get("first_name"),
        last_name=row.get("last_name"),
        email=row.get("email"),
        postal_code=row.get("postal_code"),
        token_balance=int(row.get("token_balance") or 0),
        is_disabled=bool(row.get("is_disabled")),
        is_verified=bool(row["is_verified"]) if row.get("is_verified") is not None else True,
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

    current = get_user_row(db, user_id)
    if current is None:
        raise AdminError("User not found.")
    current_name = str(current.get("username") or "")
    if is_admin_login_username(current_name) and not is_admin_login_username(cleaned):
        raise AdminError("The admin login username cannot be changed.")
    if is_reserved_username(cleaned) and not is_admin_login_username(current_name):
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
    if is_admin_login_username(getattr(user, "username", "")) and disabled:
        raise AdminError("The admin login account cannot be flagged.")
    if _is_protected_account(user) and disabled:
        raise AdminError("The justinv account cannot be flagged.")
    user.is_disabled = bool(disabled)
    db.commit()
    db.refresh(user)
    return user


def _table_names(db: Session) -> set[str]:
    bind = db.get_bind()
    if bind is None:
        return set()
    try:
        return set(inspect(bind).get_table_names())
    except Exception:
        return set()


def _exec_user_sql(db: Session, sql: str, user_id: int) -> None:
    db.execute(text(sql), {"uid": int(user_id)})


def purge_user_row(db: Session, user_id: int) -> None:
    """Hard-delete a user and dependent rows so email/username can register again.

    Live Postgres tables may lack ON DELETE CASCADE even when models declare it.
    """
    uid = int(user_id)
    tables = _table_names(db)
    nulls = (("photos", "consumed_by_id"),)
    deletes = (
        ("chat_messages", "user_id"),
        ("chat_members", "user_id"),
        ("chat_threads", "created_by_id"),
        ("photo_recipients", "user_id"),
        ("scramble_hole_scores", "posted_by_id"),
        ("scramble_members", "user_id"),
        ("challenge_players", "user_id"),
        ("activity_events", "user_id"),
        ("user_status", "user_id"),
        ("friend_requests", "requester_id"),
        ("friend_requests", "addressee_id"),
        ("token_ledger", "user_id"),
        ("photos", "sender_id"),
        ("rounds", "user_id"),
    )
    for table, column in nulls:
        if table in tables:
            _exec_user_sql(
                db,
                f"UPDATE {table} SET {column} = NULL WHERE {column} = :uid",
                uid,
            )
    if "challenges" in tables and "challenge_players" in tables:
        _exec_user_sql(
            db,
            "DELETE FROM challenge_players WHERE challenge_id IN ("
            "SELECT id FROM challenges WHERE creator_id = :uid"
            " OR source_round_id IN (SELECT id FROM rounds WHERE user_id = :uid))",
            uid,
        )
        _exec_user_sql(
            db,
            "DELETE FROM challenges WHERE creator_id = :uid"
            " OR source_round_id IN (SELECT id FROM rounds WHERE user_id = :uid)",
            uid,
        )
    if "scramble_rounds" in tables:
        if "scramble_hole_scores" in tables:
            _exec_user_sql(
                db,
                "DELETE FROM scramble_hole_scores WHERE scramble_id IN "
                "(SELECT id FROM scramble_rounds WHERE host_id = :uid)",
                uid,
            )
        if "scramble_members" in tables:
            _exec_user_sql(
                db,
                "DELETE FROM scramble_members WHERE scramble_id IN "
                "(SELECT id FROM scramble_rounds WHERE host_id = :uid)",
                uid,
            )
        if "scramble_teams" in tables:
            _exec_user_sql(
                db,
                "DELETE FROM scramble_teams WHERE scramble_id IN "
                "(SELECT id FROM scramble_rounds WHERE host_id = :uid)",
                uid,
            )
        _exec_user_sql(db, "DELETE FROM scramble_rounds WHERE host_id = :uid", uid)
    for table, column in deletes:
        if table in tables:
            _exec_user_sql(db, f"DELETE FROM {table} WHERE {column} = :uid", uid)
    _exec_user_sql(db, "DELETE FROM users WHERE id = :uid", uid)


def _is_protected_account(user: User) -> bool:
    name = str(getattr(user, "username", "") or "").strip().lower()
    return name in {ADMIN_LOGIN_USERNAME, ADMIN_PASSWORD_SOURCE_USERNAME}


def release_reusable_identities(db: Session, *, username: str, email: str) -> None:
    """Drop leftover disabled/unverified rows so the same email/username can register."""
    seen: set[int] = set()
    candidates: list[User] = []
    by_name = get_user_by_username(db, username)
    by_email = get_user_by_email(db, email)
    for user in (by_name, by_email):
        if user is None or int(user.id) in seen:
            continue
        seen.add(int(user.id))
        candidates.append(user)
    changed = False
    for user in candidates:
        if _is_protected_account(user):
            continue
        reusable = bool(getattr(user, "is_disabled", False)) or (
            getattr(user, "is_verified", True) is False
        )
        if not reusable:
            continue
        uid = int(user.id)
        db.expunge(user)
        purge_user_row(db, uid)
        changed = True
    if changed:
        db.commit()


def delete_user(db: Session, user: User) -> None:
    if is_admin_login_username(getattr(user, "username", "")):
        raise AdminError("The admin login account cannot be deleted.")
    if _is_protected_account(user):
        raise AdminError("The justinv account cannot be deleted.")
    try:
        uid = int(user.id)
        db.expunge(user)
        purge_user_row(db, uid)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise AdminError("Could not delete this user. Flag the account instead.") from exc


def save_signup_profile(
    db: Session,
    user: User,
    *,
    play_intent: str,
    play_style: str,
    skins_frequency: str,
    skins_feel: str | None = None,
    skins_pot_band: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Persist quiz answers and top the wallet up to the starting chip amount."""
    existing = parse_profile(getattr(user, "signup_profile", None))
    if existing and existing.get("starting_tokens") is not None:
        raise SignupProfileError("Signup answers already saved.")
    profile = validate_answers(
        play_intent=play_intent,
        play_style=play_style,
        skins_frequency=skins_frequency,
        skins_feel=skins_feel,
        skins_pot_band=skins_pot_band,
    )
    start = int(profile["starting_tokens"])
    current = int(user.token_balance or 0)
    credited = 0
    if current < start:
        credited = start - current
        credit_tokens(
            db,
            user,
            amount=credited,
            source=TokenSource.ADJUSTMENT,
            reason="Starting chips",
            reference=f"signup:{user.id}",
            commit=False,
        )
    profile["skins_topup_done"] = False
    user.signup_profile = dump_profile(profile)
    db.commit()
    db.refresh(user)
    return profile, credited


def apply_first_skins_topup(
    db: Session,
    user: User,
    pot_per_hole: int,
) -> dict[str, Any]:
    """Score-only / practice accounts: top wallet to ~18× pot on first skins round."""
    profile = parse_profile(getattr(user, "signup_profile", None)) or {}
    intent = str(profile.get("play_intent") or "")
    target = first_skins_topup_target(pot_per_hole)
    current = int(user.token_balance or 0)
    if intent not in {INTENT_SCORE, INTENT_PRACTICE}:
        return {
            "applied": False,
            "credited": 0,
            "target": target,
            "token_balance": current,
            "message": "Wallet already sized for skins.",
        }
    if bool(profile.get("skins_topup_done")):
        return {
            "applied": False,
            "credited": 0,
            "target": target,
            "token_balance": current,
            "message": "First-skins chips already added.",
        }
    credited = 0
    if target > current:
        credited = target - current
        credit_tokens(
            db,
            user,
            amount=credited,
            source=TokenSource.REWARD,
            reason="First skins round chips",
            reference=f"skins-topup:{user.id}",
            commit=False,
        )
    profile["skins_topup_done"] = True
    profile["skins_topup_pot"] = int(pot_per_hole)
    user.signup_profile = dump_profile(profile)
    db.commit()
    db.refresh(user)
    return {
        "applied": credited > 0,
        "credited": credited,
        "target": target,
        "token_balance": int(user.token_balance or 0),
        "message": (
            f"Added {credited:,} chips so you can play this round."
            if credited
            else "Wallet already covers this pot."
        ),
    }
