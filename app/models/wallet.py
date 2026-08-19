from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.user import User


class TokenDirection(str, enum.Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class TokenSource(str, enum.Enum):
    """Machine-readable origin of a ledger entry.

    Credit sources (awards) can be used by later round/skins/challenge code
    via ``award_tokens`` without new schema.
    """

    REWARD = "reward"
    WELCOME = "welcome"
    ROUND_COMPLETE_9 = "round_complete_9"
    ROUND_COMPLETE_18 = "round_complete_18"
    PAR = "par"
    BIRDIE = "birdie"
    EAGLE = "eagle"
    SKINS_WIN = "skins_win"
    CHALLENGE_WIN = "challenge_win"
    WAGER = "wager"
    FORFEIT = "forfeit"
    PURCHASE = "purchase"
    ADJUSTMENT = "adjustment"


CREDIT_SOURCES = frozenset(
    {
        TokenSource.REWARD,
        TokenSource.WELCOME,
        TokenSource.ROUND_COMPLETE_9,
        TokenSource.ROUND_COMPLETE_18,
        TokenSource.PAR,
        TokenSource.BIRDIE,
        TokenSource.EAGLE,
        TokenSource.SKINS_WIN,
        TokenSource.CHALLENGE_WIN,
        TokenSource.ADJUSTMENT,
    }
)

DEBIT_SOURCES = frozenset(
    {
        TokenSource.WAGER,
        TokenSource.FORFEIT,
        TokenSource.PURCHASE,
        TokenSource.ADJUSTMENT,
    }
)


class TokenLedger(Base):
    __tablename__ = "token_ledger"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_token_ledger_amount_positive"),
        Index("ix_token_ledger_user_created", "user_id", "created_at"),
        Index("ix_token_ledger_user_id", "user_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[TokenDirection] = mapped_column(
        Enum(TokenDirection, native_enum=False, length=16),
        nullable=False,
    )
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[TokenSource] = mapped_column(
        Enum(TokenSource, native_enum=False, length=32),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()
