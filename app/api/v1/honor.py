from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.honor import (
    HonorFriendsResponse,
    HonorHotResponse,
    HonorSnapshot,
    HonorSyncRequest,
)
from app.services.honor import friends_board, hot_list, recompute_and_save

router = APIRouter(prefix="/honor", tags=["honor"])


@router.get("", response_model=HonorSnapshot)
def get_own_honor(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HonorSnapshot:
    return HonorSnapshot.model_validate(recompute_and_save(db, current_user))


@router.put("/sync", response_model=HonorSnapshot)
def put_honor_sync(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: HonorSyncRequest | None = Body(default=None),
) -> HonorSnapshot:
    payload = body or HonorSyncRequest()
    snap = recompute_and_save(
        db,
        current_user,
        skins_taken=payload.skins_taken,
        birdies=payload.birdies,
        round_count=payload.round_count,
        friend_count=payload.friend_count,
        challenge_count=payload.challenge_count,
    )
    return HonorSnapshot.model_validate(snap)


@router.get("/friends", response_model=HonorFriendsResponse)
def get_honor_friends(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> HonorFriendsResponse:
    return HonorFriendsResponse.model_validate(friends_board(db, current_user))


@router.get("/hot", response_model=HonorHotResponse)
def get_honor_hot(
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> HonorHotResponse:
    return HonorHotResponse.model_validate(hot_list(db, limit=limit))
