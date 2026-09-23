"""Solo-card honor credits from the stored card.

Finish is +10. Each hole is par +1, birdie +2, eagle or better +3,
bogey or worse 0. A 9-hole or abandoned card pays 0. The same round
is credited once. This uses scores and pars already on the card.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.round import Round
from app.models.user import User
from app.models.wallet import TokenDirection, TokenLedger, TokenSource

FINISH_18 = 10
_EASTERN = ZoneInfo("America/New_York")
_BAY_HILL = "bay hill club lodge championship course"
_BACKFILL_YEAR = 2026
_BACKFILL_MONTH = 9
_BACKFILL_DAY = 19
_BACKFILL_TOTAL = 86


def honor_reference(round_id: int) -> str:
    return f"honor:round:{int(round_id)}"


def _whole_number(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not value.is_integer():
            return None
        return int(value)
    try:
        text = str(value).strip()
        if text.endswith(".0"):
            text = text[:-2]
        return int(text)
    except (TypeError, ValueError):
        return None


def hole_honor(score: object, par: object) -> int:
    score_n = _whole_number(score)
    par_n = _whole_number(par)
    if score_n is None or par_n is None:
        return 0
    if score_n < 1 or par_n < 3:
        return 0
    under = par_n - score_n
    if under >= 2:
        return 3
    if under == 1:
        return 2
    if under == 0:
        return 1
    return 0


def is_completed_18(record: Round) -> bool:
    if int(getattr(record, "num_holes", 0) or 0) != 18:
        return False
    scores = list(record.scores or [])
    if len(scores) != 18:
        return False
    for score in scores:
        score_n = _whole_number(score)
        if score_n is None or score_n < 1:
            return False
    return True


def solo_honor_amount(record: Round) -> int:
    if not is_completed_18(record):
        return 0
    amount = FINISH_18
    pars = list(record.pars or [])
    if len(pars) != 18:
        return amount
    for score, par in zip(list(record.scores or []), pars):
        amount += hole_honor(score, par)
    return amount


def _existing_credit(db: Session, user_id: int, round_id: int) -> TokenLedger | None:
    return db.scalar(
        select(TokenLedger).where(
            TokenLedger.user_id == user_id,
            TokenLedger.reference == honor_reference(round_id),
            TokenLedger.direction == TokenDirection.CREDIT,
        )
    )


def reverse_solo_honor(db: Session, user: User, round_id: int) -> TokenLedger | None:
    """Take back Honor credited for this card. Nothing is owed when it was unpaid."""
    ref = honor_reference(round_id)
    rows = list(
        db.scalars(
            select(TokenLedger).where(
                TokenLedger.user_id == user.id,
                TokenLedger.reference == ref,
            )
        ).all()
    )
    credited = sum(
        int(row.amount) for row in rows if row.direction == TokenDirection.CREDIT
    )
    debited = sum(
        int(row.amount) for row in rows if row.direction == TokenDirection.DEBIT
    )
    remaining = credited - debited
    if remaining <= 0:
        return None
    from app.services.wallet import debit_tokens

    return debit_tokens(
        db,
        user,
        amount=remaining,
        source=TokenSource.ADJUSTMENT,
        reason="Solo honor reversed",
        reference=ref,
        commit=False,
    )


def settle_solo_round(db: Session, user: User, record: Round) -> TokenLedger | None:
    """Credit honor for one completed 18-hole solo card. Repeat calls pay nothing."""
    if record.id is None or int(record.user_id) != int(user.id):
        return None
    existing = _existing_credit(db, user.id, record.id)
    if existing is not None:
        return existing
    amount = solo_honor_amount(record)
    if amount <= 0:
        return None
    from app.services.wallet import credit_tokens

    try:
        return credit_tokens(
            db,
            user,
            amount=amount,
            source=TokenSource.ROUND_COMPLETE_18,
            reason="Solo honor",
            reference=honor_reference(record.id),
        )
    except IntegrityError:
        db.rollback()
        return _existing_credit(db, user.id, record.id)


def _on_sep_19_2026(value: datetime | None) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        if (
            value.year == _BACKFILL_YEAR
            and value.month == _BACKFILL_MONTH
            and value.day == _BACKFILL_DAY
        ):
            return True
        value = value.replace(tzinfo=timezone.utc)
    utc = value.astimezone(timezone.utc)
    eastern = value.astimezone(_EASTERN)

    def _hit(moment: datetime) -> bool:
        return (
            moment.year == _BACKFILL_YEAR
            and moment.month == _BACKFILL_MONTH
            and moment.day == _BACKFILL_DAY
        )

    return _hit(utc) or _hit(eastern)


def _is_bay_hill(name: str | None) -> bool:
    folded = " ".join((name or "").strip().casefold().split())
    return folded == _BAY_HILL


def _is_backfill_user(user: User) -> bool:
    expected = get_settings().ADMIN_USERNAME.strip().casefold()
    return (user.username or "").strip().casefold() == expected


def backfill_bay_hill_honor(db: Session | None = None) -> int:
    """Pay Bay Hill 86 (18) on Sep 19, 2026 for the admin user when it is still unpaid.

    Other cards are left alone. Returns how many new credits were written.
    """
    own_session = db is None
    if db is None:
        from app.database import SessionLocal

        db = SessionLocal()
    assert db is not None
    try:
        user = db.scalar(
            select(User).where(
                func.lower(User.username) == get_settings().ADMIN_USERNAME.strip().casefold()
            )
        )
        if user is None or not _is_backfill_user(user):
            return 0
        rows = list(
            db.scalars(
                select(Round).where(
                    Round.user_id == user.id,
                    Round.num_holes == 18,
                    Round.total == _BACKFILL_TOTAL,
                )
            ).all()
        )
        paid = 0
        for record in rows:
            if not _is_bay_hill(record.course_name):
                continue
            if not _on_sep_19_2026(record.created_at):
                continue
            if not is_completed_18(record):
                continue
            already = _existing_credit(db, user.id, record.id)
            entry = settle_solo_round(db, user, record)
            if already is None and entry is not None:
                paid += 1
        return paid
    finally:
        if own_session:
            db.close()
