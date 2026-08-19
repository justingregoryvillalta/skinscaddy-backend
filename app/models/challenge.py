from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base
from app.models.round import Round
from app.models.user import User


class ChallengeStatus(str, enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    EXPIRED = "expired"
    FORFEITED = "forfeited"


class ChallengePlayerRole(str, enum.Enum):
    HOST = "host"
    OPPONENT = "opponent"


class ChallengePlayerStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    COMPLETED = "completed"
    FORFEITED = "forfeited"


class Challenge(Base):
    __tablename__ = "challenges"
    __table_args__ = (
        CheckConstraint("wager_amount >= 0", name="ck_challenges_wager_nonnegative"),
        CheckConstraint(
            "duration_weeks >= 1 AND duration_weeks <= 4",
            name="ck_challenges_weeks_range",
        ),
        CheckConstraint("pot_amount >= 0", name="ck_challenges_pot_nonnegative"),
        Index("ix_challenges_status_deadline", "status", "deadline"),
        Index("ix_challenges_creator", "creator_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_round_id: Mapped[int] = mapped_column(
        ForeignKey("rounds.id", ondelete="RESTRICT"),
        nullable=False,
    )
    course_name: Mapped[str] = mapped_column(String(120), nullable=False)
    num_holes: Mapped[int] = mapped_column(Integer, nullable=False)
    wager_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ChallengeStatus] = mapped_column(
        Enum(ChallengeStatus, native_enum=False, length=16),
        nullable=False,
        default=ChallengeStatus.PENDING,
    )
    pot_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped[User] = relationship(foreign_keys=[creator_id])
    source_round: Mapped[Round] = relationship()
    players: Mapped[list[ChallengePlayer]] = relationship(
        back_populates="challenge",
        cascade="all, delete-orphan",
        order_by="ChallengePlayer.id",
    )


class ChallengePlayer(Base):
    __tablename__ = "challenge_players"
    __table_args__ = (
        UniqueConstraint("challenge_id", "user_id", name="uq_challenge_players_user"),
        Index("ix_challenge_players_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    challenge_id: Mapped[int] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[ChallengePlayerRole] = mapped_column(
        Enum(ChallengePlayerRole, native_enum=False, length=16),
        nullable=False,
    )
    status: Mapped[ChallengePlayerStatus] = mapped_column(
        Enum(ChallengePlayerStatus, native_enum=False, length=16),
        nullable=False,
        default=ChallengePlayerStatus.PENDING,
    )
    scores: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    escrowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    escrow_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    challenge: Mapped[Challenge] = relationship(back_populates="players")
    user: Mapped[User] = relationship()
