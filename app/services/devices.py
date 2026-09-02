from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.device import DeviceToken
from app.models.user import User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def register_device_token(
    db: Session,
    user: User,
    *,
    token: str,
    platform: str = "android",
) -> DeviceToken:
    raw = (token or "").strip()
    plat = (platform or "android").strip().lower() or "android"
    if plat not in {"android", "ios", "web"}:
        plat = "android"
    row = db.scalar(select(DeviceToken).where(DeviceToken.token == raw))
    if row is None:
        row = DeviceToken(user_id=user.id, token=raw, platform=plat, updated_at=_now())
        db.add(row)
    else:
        row.user_id = user.id
        row.platform = plat
        row.updated_at = _now()
    db.commit()
    db.refresh(row)
    return row


def unregister_device_token(db: Session, user: User, token: str) -> bool:
    raw = (token or "").strip()
    row = db.scalar(
        select(DeviceToken).where(
            DeviceToken.token == raw,
            DeviceToken.user_id == user.id,
        )
    )
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def tokens_for_users(db: Session, user_ids: list[int]) -> list[DeviceToken]:
    ids = sorted({int(i) for i in user_ids if i})
    if not ids:
        return []
    return list(db.scalars(select(DeviceToken).where(DeviceToken.user_id.in_(ids))).all())


def delete_token_value(db: Session, token: str) -> None:
    raw = (token or "").strip()
    row = db.scalar(select(DeviceToken).where(DeviceToken.token == raw))
    if row is None:
        return
    db.delete(row)
    db.commit()
