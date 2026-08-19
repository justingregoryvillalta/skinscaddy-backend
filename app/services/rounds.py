from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.round import Round
from app.models.user import User


class RoundError(ValueError):
    pass


class RoundNotFoundError(RoundError):
    pass


class RoundForbiddenError(RoundError):
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
