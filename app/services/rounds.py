from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.round import Round
from app.models.user import User
from app.services.wallet import InsufficientTokensError


class RoundError(ValueError):
    pass


class RoundNotFoundError(RoundError):
    pass


class RoundForbiddenError(RoundError):
    pass


class RoundConflictError(RoundError):
    pass


def create_round(
    db: Session,
    actor: User,
    *,
    course_name: str,
    course_id: str | None,
    num_holes: int,
    scores: list[int],
    pars: list[int] | None,
) -> Round:
    record = Round(
        user_id=actor.id,
        course_name=course_name,
        course_id=course_id,
        num_holes=num_holes,
        scores=list(scores),
        pars=list(pars) if pars is not None else None,
        total=sum(int(s) for s in scores),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    try:
        from app.services.honor_settle import settle_solo_round

        settle_solo_round(db, actor, record)
    except Exception:
        db.rollback()
    try:
        from app.services.honor import recompute_and_save

        recompute_and_save(db, actor)
    except Exception:
        pass
    return get_round(db, record.id) or record


def get_round(db: Session, round_id: int) -> Round | None:
    return db.scalar(
        select(Round).options(selectinload(Round.user)).where(Round.id == round_id)
    )


def get_owned_round(db: Session, actor: User, round_id: int) -> Round:
    record = get_round(db, round_id)
    if record is None:
        raise RoundNotFoundError("Round not found.")
    if record.user_id != actor.id:
        raise RoundForbiddenError("You can only use your own completed rounds.")
    return record


def list_rounds(db: Session, actor: User) -> list[Round]:
    return list(
        db.scalars(
            select(Round)
            .options(selectinload(Round.user))
            .where(Round.user_id == actor.id)
            .order_by(Round.created_at.desc(), Round.id.desc())
        ).all()
    )


def delete_round(db: Session, actor: User, round_id: int) -> None:
    """Owner removes one completed solo card and reverses its Honor credit.

    A challenge that still points at the card is a side game: leave the card
    and the Honor payment in place.
    """
    record = get_round(db, round_id)
    if record is None:
        raise RoundNotFoundError("Round not found.")
    if int(record.user_id) != int(actor.id):
        raise RoundForbiddenError("You can only delete your own rounds.")

    from app.models.challenge import Challenge

    attached = db.scalar(
        select(Challenge.id).where(Challenge.source_round_id == record.id).limit(1)
    )
    if attached is not None:
        raise RoundConflictError("A side game is attached to this round.")

    from app.models.chat import ChatMessage
    from app.services.honor_settle import reverse_solo_honor

    try:
        reverse_solo_honor(db, actor, record.id)
        db.execute(
            update(ChatMessage)
            .where(ChatMessage.round_id == record.id)
            .values(round_id=None)
        )
        db.delete(record)
        db.commit()
    except InsufficientTokensError:
        db.rollback()
        raise RoundConflictError(
            "Not enough tokens to reverse the Honor paid for this round."
        ) from None
    except IntegrityError:
        db.rollback()
        raise RoundConflictError("A side game is attached to this round.") from None

    try:
        from app.services.honor import recompute_and_save

        recompute_and_save(db, actor)
    except Exception:
        pass
