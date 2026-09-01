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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base
from app.models.user import User


class ChatThreadKind(str, enum.Enum):
    DIRECT = "direct"
    GROUP = "group"


class ChatMessageKind(str, enum.Enum):
    TEXT = "text"
    ROUND = "round"


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        UniqueConstraint("pair_key", name="uq_chat_threads_pair_key"),
        Index("ix_chat_threads_kind_updated", "kind", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[ChatThreadKind] = mapped_column(
        Enum(ChatThreadKind, native_enum=False, length=16),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    pair_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
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

    created_by: Mapped[User] = relationship()
    members: Mapped[list[ChatMember]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="thread",
        cascade="all, delete-orphan",
    )


class ChatMember(Base):
    __tablename__ = "chat_members"
    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uq_chat_members_thread_user"),
        Index("ix_chat_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
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

    thread: Mapped[ChatThread] = relationship(back_populates="members")
    user: Mapped[User] = relationship()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_thread_created", "thread_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("chat_threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[ChatMessageKind] = mapped_column(
        Enum(ChatMessageKind, native_enum=False, length=16),
        nullable=False,
        default=ChatMessageKind.TEXT,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    round_id: Mapped[int | None] = mapped_column(
        ForeignKey("rounds.id", ondelete="SET NULL"),
        nullable=True,
    )
    snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    thread: Mapped[ChatThread] = relationship(back_populates="messages")
    user: Mapped[User] = relationship()
