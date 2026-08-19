from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.round import Round
from app.models.user import User
from app.schemas.round import CreateRoundRequest, RoundListResponse, RoundPublic
from app.services.rounds import (
    RoundForbiddenError,
    RoundNotFoundError,
    create_round,
    get_round,
    list_rounds,
)

router = APIRouter(prefix="/rounds", tags=["rounds"])


@router.post("", response_model=RoundPublic, status_code=status.HTTP_201_CREATED)
def post_round(
    body: CreateRoundRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Round:
    record = create_round(
        db,
        current_user,
        course_name=body.course_name,
        course_id=body.course_id,
        num_holes=body.num_holes,
        scores=body.scores,
        pars=body.pars,
    )
    return record


@router.get("", response_model=RoundListResponse)
def get_rounds(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> RoundListResponse:
    return RoundListResponse(
        rounds=[RoundPublic.model_validate(row) for row in list_rounds(db, current_user)]
    )


@router.get("/{round_id}", response_model=RoundPublic)
def get_one_round(
    round_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Round:
    record = get_round(db, round_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Round not found.")
    if record.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own rounds.",
        )
    return record
