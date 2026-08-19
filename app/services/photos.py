from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.models.photo import Photo, PhotoKind, PhotoRecipient, PhotoStatus
from app.models.user import User
from app.services.challenges import (
    ChallengeForbiddenError,
    ChallengeNotFoundError,
    get_challenge,
)
from app.services.friends import UserNotFoundError, are_friends
from app.services.users import get_user_by_username

ALLOWED_EXPIRY_DAYS = {7, 14}
ALLOWED_TYPES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/webp": (".webp", b"RIFF"),
}


class PhotoError(ValueError):
    pass


class PhotoNotFoundError(PhotoError):
    pass


class PhotoForbiddenError(PhotoError):
    pass


class PhotoGoneError(PhotoError):
    pass


class PhotoInvalidError(PhotoError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_options():
    return (
        selectinload(Photo.sender),
        selectinload(Photo.recipients).selectinload(PhotoRecipient.user),
    )


def photo_url(photo_id: int) -> str:
    return f"/api/v1/photos/{photo_id}/file"


def storage_path(storage_name: str) -> Path:
    return get_settings().photo_dir / storage_name


def _sniff_image(data: bytes, _declared: str) -> str:
    if len(data) < 12:
        raise PhotoInvalidError("File is not a supported image.")
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise PhotoInvalidError("File is not a JPEG, PNG, or WebP image.")


def _unlink(storage_name: str | None) -> None:
    if not storage_name:
        return
    path = storage_path(storage_name)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def purge_expired_photos(db: Session) -> int:
    now = _now()
    rows = list(
        db.scalars(
            select(Photo).where(
                Photo.status == PhotoStatus.AVAILABLE,
                Photo.expires_at <= now,
            )
        ).all()
    )
    for photo in rows:
        _unlink(photo.storage_name)
        photo.status = PhotoStatus.EXPIRED
    if rows:
        db.commit()
    return len(rows)


def get_photo(db: Session, photo_id: int) -> Photo | None:
    return db.scalar(select(Photo).options(*_load_options()).where(Photo.id == photo_id))


def _require_access(photo: Photo, actor: User) -> None:
    if photo.sender_id == actor.id:
        return
    if any(row.user_id == actor.id for row in photo.recipients):
        return
    raise PhotoForbiddenError("You cannot access this photo.")


def _recipient_users_for_challenge(db: Session, actor: User, challenge_id: int) -> list[User]:
    challenge = get_challenge(db, challenge_id)
    if challenge is None:
        raise ChallengeNotFoundError("Challenge not found.")
    member_ids = {player.user_id for player in challenge.players}
    if actor.id not in member_ids:
        raise ChallengeForbiddenError("You are not part of this challenge.")
    others: list[User] = []
    for player in challenge.players:
        if player.user_id == actor.id:
            continue
        if player.user is not None:
            others.append(player.user)
    if not others:
        raise PhotoInvalidError("A challenge photo needs at least one other player.")
    return others


def _recipient_users_for_prop(db: Session, actor: User, usernames: list[str]) -> list[User]:
    cleaned: list[User] = []
    seen: set[int] = set()
    for raw in usernames:
        name = raw.strip()
        if not name:
            continue
        user = get_user_by_username(db, name)
        if user is None:
            raise UserNotFoundError(f"User '{name}' not found.")
        if user.id == actor.id:
            raise PhotoInvalidError("You cannot send a photo only to yourself.")
        if not are_friends(db, actor.id, user.id):
            raise PhotoForbiddenError(f"@{user.username} is not on your friends list.")
        if user.id in seen:
            continue
        seen.add(user.id)
        cleaned.append(user)
    if not cleaned:
        raise PhotoInvalidError("Pick at least one friend to receive this photo.")
    if len(cleaned) > 3:
        raise PhotoInvalidError("A prop photo can go to at most 3 friends.")
    return cleaned


def upload_photo(
    db: Session,
    actor: User,
    *,
    data: bytes,
    filename: str | None,
    declared_type: str,
    kind: PhotoKind,
    challenge_id: int | None,
    recipient_usernames: list[str],
    hole: int | None,
    caption: str | None,
    expires_in_days: int,
) -> Photo:
    purge_expired_photos(db)
    settings = get_settings()
    if not data:
        raise PhotoInvalidError("Photo file is empty.")
    if len(data) > settings.PHOTO_MAX_BYTES:
        raise PhotoInvalidError(
            f"Photo is too large. Max size is {settings.PHOTO_MAX_BYTES // (1024 * 1024)} MB."
        )
    if expires_in_days not in ALLOWED_EXPIRY_DAYS:
        raise PhotoInvalidError("Expiry must be 7 or 14 days.")

    content_type = _sniff_image(data, declared_type)
    ext = ALLOWED_TYPES[content_type][0]
    if kind == PhotoKind.CHALLENGE:
        if challenge_id is None:
            raise PhotoInvalidError("challenge_id is required for challenge photos.")
        recipients = _recipient_users_for_challenge(db, actor, challenge_id)
    else:
        recipients = _recipient_users_for_prop(db, actor, recipient_usernames)
        challenge_id = None

    now = _now()
    storage_name = f"{uuid.uuid4().hex}{ext}"
    dest = storage_path(storage_name)
    dest.write_bytes(data)

    photo = Photo(
        sender_id=actor.id,
        kind=kind,
        status=PhotoStatus.AVAILABLE,
        challenge_id=challenge_id,
        hole=hole,
        caption=(caption.strip() if caption else None) or None,
        content_type=content_type,
        original_filename=(filename or "")[:200] or None,
        byte_size=len(data),
        storage_name=storage_name,
        expires_in_days=expires_in_days,
        expires_at=now + timedelta(days=expires_in_days),
    )
    db.add(photo)
    db.flush()
    for user in recipients:
        db.add(PhotoRecipient(photo_id=photo.id, user_id=user.id))
    db.commit()
    loaded = get_photo(db, photo.id)
    assert loaded is not None
    return loaded


def list_photos(
    db: Session,
    actor: User,
    *,
    challenge_id: int | None = None,
    kind: PhotoKind | None = None,
) -> list[Photo]:
    purge_expired_photos(db)
    query = (
        select(Photo)
        .options(*_load_options())
        .outerjoin(PhotoRecipient)
        .where(
            or_(Photo.sender_id == actor.id, PhotoRecipient.user_id == actor.id),
        )
        .order_by(Photo.created_at.desc(), Photo.id.desc())
        .distinct()
    )
    if challenge_id is not None:
        query = query.where(Photo.challenge_id == challenge_id)
    if kind is not None:
        query = query.where(Photo.kind == kind)
    return list(db.scalars(query).all())


def get_visible_photo(db: Session, actor: User, photo_id: int) -> Photo:
    purge_expired_photos(db)
    photo = get_photo(db, photo_id)
    if photo is None:
        raise PhotoNotFoundError("Photo not found.")
    _require_access(photo, actor)
    return photo


def read_photo_bytes(db: Session, actor: User, photo_id: int) -> tuple[bytes, str, bool]:
    """Return (bytes, content_type, consumed_this_view). Sender does not consume."""
    purge_expired_photos(db)
    photo = db.scalar(
        select(Photo)
        .options(*_load_options())
        .where(Photo.id == photo_id)
        .with_for_update()
    )
    if photo is None:
        raise PhotoNotFoundError("Photo not found.")
    _require_access(photo, actor)

    if photo.status != PhotoStatus.AVAILABLE:
        raise PhotoGoneError("This photo is no longer available.")
    expires = photo.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= _now():
        _unlink(photo.storage_name)
        photo.status = PhotoStatus.EXPIRED
        db.commit()
        raise PhotoGoneError("This photo has expired.")

    path = storage_path(photo.storage_name)
    if not path.is_file():
        photo.status = PhotoStatus.CONSUMED
        db.commit()
        raise PhotoGoneError("This photo is no longer available.")

    data = path.read_bytes()
    consumed = False
    if actor.id != photo.sender_id:
        recipient = next((row for row in photo.recipients if row.user_id == actor.id), None)
        if recipient is not None and recipient.viewed_at is None:
            recipient.viewed_at = _now()
        _unlink(photo.storage_name)
        photo.status = PhotoStatus.CONSUMED
        photo.consumed_at = _now()
        photo.consumed_by_id = actor.id
        consumed = True
        db.commit()
    return data, photo.content_type, consumed


def to_public(photo: Photo) -> dict:
    return {
        "id": photo.id,
        "kind": photo.kind,
        "status": photo.status,
        "url": photo_url(photo.id),
        "available": photo.status == PhotoStatus.AVAILABLE,
        "view_once": True,
        "challenge_id": photo.challenge_id,
        "hole": photo.hole,
        "caption": photo.caption,
        "content_type": photo.content_type,
        "byte_size": photo.byte_size,
        "expires_in_days": photo.expires_in_days,
        "expires_at": photo.expires_at,
        "consumed_at": photo.consumed_at,
        "created_at": photo.created_at,
        "sender": photo.sender,
        "recipients": [
            {"user": row.user, "viewed_at": row.viewed_at} for row in photo.recipients
        ],
    }
