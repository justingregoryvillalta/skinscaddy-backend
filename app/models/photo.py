from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.user import User


class PhotoKind(str, enum.Enum):
    CHALLENGE = "challenge"
    PROP = "prop"


class PhotoStatus(str, enum.Enum):
    AVAILABLE = "available"
    CONSUMED = "consumed"
    EXPIRED = "expired"


class Photo(Base):
    __tablename__ = "photos"
    __table_args__ = (
        Index("ix_photos_sender_created", "sender_id", "created_at"),
        Index("ix_photos_challenge", "challenge_id"),
        Index("ix_photos_status_expires", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sender_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[PhotoKind] = mapped_column(
        Enum(PhotoKind, native_enum=False, length=16),
        nullable=False,
    )
    status: Mapped[PhotoStatus] = mapped_column(
        Enum(PhotoStatus, native_enum=False, length=16),
        nullable=False,
        default=PhotoStatus.AVAILABLE,
    )
    challenge_id: Mapped[int | None] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"),
        nullable=True,
    )
    hole: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_name: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    expires_in_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    sender: Mapped[User] = relationship(foreign_keys=[sender_id])
    consumed_by: Mapped[User | None] = relationship(foreign_keys=[consumed_by_id])
    recipients: Mapped[list[PhotoRecipient]] = relationship(
        back_populates="photo",
        cascade="all, delete-orphan",
    )


class PhotoRecipient(Base):
    __tablename__ = "photo_recipients"
    __table_args__ = (Index("ix_photo_recipients_user", "user_id"),)

    photo_id: Mapped[int] = mapped_column(
        ForeignKey("photos.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    photo: Mapped[Photo] = relationship(back_populates="recipients")
    user: Mapped[User] = relationship()
