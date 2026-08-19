from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
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
from app.models.user import User


class ScrambleStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


class ScrambleRound(Base):
    __tablename__ = "scramble_rounds"
    __table_args__ = (
        UniqueConstraint("join_code", name="uq_scramble_join_code"),
        Index("ix_scramble_host_created", "host_id", "created_at"),
        Index("ix_scramble_status_updated", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    join_code: Mapped[str] = mapped_column(String(8), nullable=False)
    host_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_name: Mapped[str] = mapped_column(String(120), nullable=False)
    course_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    num_holes: Mapped[int] = mapped_column(Integer, nullable=False)
    wager_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ScrambleStatus] = mapped_column(
        Enum(ScrambleStatus, native_enum=False, length=16),
        nullable=False,
        default=ScrambleStatus.ACTIVE,
    )
    pars: Mapped[list | None] = mapped_column(JSON, nullable=True)
    skin_unit: Mapped[int] = mapped_column(Integer, nullable=False)
    skin_pot: Mapped[int] = mapped_column(Integer, nullable=False)
    skin_stack: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    skin_results: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
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

    host: Mapped[User] = relationship(foreign_keys=[host_id])
    teams: Mapped[list[ScrambleTeam]] = relationship(
        back_populates="scramble",
        cascade="all, delete-orphan",
        order_by="ScrambleTeam.index",
    )
    members: Mapped[list[ScrambleMember]] = relationship(
        back_populates="scramble",
        cascade="all, delete-orphan",
    )
    scores: Mapped[list[ScrambleHoleScore]] = relationship(
        back_populates="scramble",
        cascade="all, delete-orphan",
    )


class ScrambleTeam(Base):
    __tablename__ = "scramble_teams"
    __table_args__ = (
        UniqueConstraint("scramble_id", "index", name="uq_scramble_team_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scramble_id: Mapped[int] = mapped_column(
        ForeignKey("scramble_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(40), nullable=False)
    start_hole: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    skins_won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    scramble: Mapped[ScrambleRound] = relationship(back_populates="teams")
    members: Mapped[list[ScrambleMember]] = relationship(back_populates="team")
    scores: Mapped[list[ScrambleHoleScore]] = relationship(back_populates="team")


class ScrambleMember(Base):
    __tablename__ = "scramble_members"
    __table_args__ = (
        UniqueConstraint("scramble_id", "user_id", name="uq_scramble_member"),
        Index("ix_scramble_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scramble_id: Mapped[int] = mapped_column(
        ForeignKey("scramble_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("scramble_teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    scramble: Mapped[ScrambleRound] = relationship(back_populates="members")
    team: Mapped[ScrambleTeam] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class ScrambleHoleScore(Base):
    __tablename__ = "scramble_hole_scores"
    __table_args__ = (
        UniqueConstraint(
            "scramble_id",
            "team_id",
            "hole",
            name="uq_scramble_team_hole",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scramble_id: Mapped[int] = mapped_column(
        ForeignKey("scramble_rounds.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[int] = mapped_column(
        ForeignKey("scramble_teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    hole: Mapped[int] = mapped_column(Integer, nullable=False)
    strokes: Mapped[int] = mapped_column(Integer, nullable=False)
    posted_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    scramble: Mapped[ScrambleRound] = relationship(back_populates="scores")
    team: Mapped[ScrambleTeam] = relationship(back_populates="scores")
    posted_by: Mapped[User] = relationship()
