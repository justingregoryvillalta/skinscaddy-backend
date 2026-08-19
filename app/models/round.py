from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base
from app.models.user import User


class Round(Base):
    """A completed solo card a user can attach to a challenge."""

    __tablename__ = "rounds"
    __table_args__ = (Index("ix_rounds_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    course_name: Mapped[str] = mapped_column(String(120), nullable=False)
    course_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    num_holes: Mapped[int] = mapped_column(Integer, nullable=False)
    scores: Mapped[list] = mapped_column(JSON, nullable=False)
    pars: Mapped[list | None] = mapped_column(JSON, nullable=True)
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship()
