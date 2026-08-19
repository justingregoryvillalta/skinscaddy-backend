from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.feed import (
    ActivityPublic,
    CreateActivityRequest,
    FriendsFeedResponse,
    LiveStatusPublic,
    UpdateStatusRequest,
)
from app.services.feed import (
    InvalidStatusError,
    activity_view,
    build_friends_feed,
    get_own_status,
    live_view,
    record_activity,
    update_status,
)

router = APIRouter(tags=["feed"])


def _status_public(row, user: User, *, owner: bool) -> LiveStatusPublic:
    return LiveStatusPublic.model_validate(live_view(row, user, viewer_is_owner=owner))


@router.get("/status", response_model=LiveStatusPublic)
def read_status(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LiveStatusPublic:
    row = get_own_status(db, current_user)
    if row.user is None:
        row.user = current_user
    return _status_public(row, current_user, owner=True)


@router.put("/status", response_model=LiveStatusPublic)
def put_status(
    body: UpdateStatusRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> LiveStatusPublic:
    try:
        row = update_status(
            db,
            current_user,
            state=body.state,
            mode=body.mode,
            course_name=body.course_name,
            course_id=body.course_id,
            hole=body.hole,
            holes_completed=body.holes_completed,
            num_holes=body.num_holes,
            scores=body.scores,
            total=body.total,
            privacy=body.privacy,
            allow_join=body.allow_join,
        )
    except InvalidStatusError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _status_public(row, current_user, owner=True)


@router.get("/feed", response_model=FriendsFeedResponse)
def read_feed(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    activity_limit: Annotated[int, Query(ge=1, le=100)] = 30,
    since: Annotated[datetime | None, Query()] = None,
) -> FriendsFeedResponse:
    live, activity, generated_at = build_friends_feed(
        db,
        current_user,
        activity_limit=activity_limit,
        since=since,
    )
    return FriendsFeedResponse(
        generated_at=generated_at,
        live=[LiveStatusPublic.model_validate(item) for item in live],
        activity=[ActivityPublic.model_validate(item) for item in activity],
    )


@router.post("/feed/events", response_model=ActivityPublic, status_code=status.HTTP_201_CREATED)
def post_event(
    body: CreateActivityRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ActivityPublic:
    event = record_activity(
        db,
        current_user,
        body.kind,
        course_name=body.course_name,
        mode=body.mode,
        hole=body.hole,
        total=body.total,
        scores=body.scores,
        privacy=body.privacy,
        summary=body.summary,
        payload=body.payload,
    )
    return ActivityPublic.model_validate(
        activity_view(event, current_user, viewer_is_owner=True)
    )
