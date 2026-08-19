from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base
from app.models.user import User


class LiveState(str, enum.Enum):
    IDLE = "idle"
    PLAYING = "playing"
    FINISHED = "finished"


class PlayMode(str, enum.Enum):
    SOLO = "solo"
    SKINS = "skins"
    SCRAMBLE = "scramble"


class PrivacyMode(str, enum.Enum):
    FULL = "full"
    LIMITED = "limited"


class ActivityKind(str, enum.Enum):
    STARTED_ROUND = "started_round"
    FINISHED_ROUND = "finished_round"
    WON_SKINS = "won_skins"
    WON_CHALLENGE = "won_challenge"


class UserStatus(Base):
    __tablename__ = "user_status"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state: Mapped[LiveState] = mapped_column(
        Enum(LiveState, native_enum=False, length=16),
        nullable=False,
        default=LiveState.IDLE,
    )
    mode: Mapped[PlayMode | None] = mapped_column(
        Enum(PlayMode, native_enum=False, length=16),
        nullable=True,
    )
    course_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    course_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hole: Mapped[int | None] = mapped_column(Integer, nullable=True)
    holes_completed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_holes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scores: Mapped[list | None] = mapped_column(JSON, nullable=True)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    privacy: Mapped[PrivacyMode] = mapped_column(
        Enum(PrivacyMode, native_enum=False, length=16),
        nullable=False,
        default=PrivacyMode.FULL,
    )
    allow_join: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()


class ActivityEvent(Base):
    __tablename__ = "activity_events"
    __table_args__ = (
        Index("ix_activity_events_user_created", "user_id", "created_at"),
        Index("ix_activity_events_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ActivityKind] = mapped_column(
        Enum(ActivityKind, native_enum=False, length=32),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    course_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mode: Mapped[PlayMode | None] = mapped_column(
        Enum(PlayMode, native_enum=False, length=16),
        nullable=True,
    )
    hole: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scores: Mapped[list | None] = mapped_column(JSON, nullable=True)
    privacy: Mapped[PrivacyMode] = mapped_column(
        Enum(PrivacyMode, native_enum=False, length=16),
        nullable=False,
        default=PrivacyMode.FULL,
    )
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()
