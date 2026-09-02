from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.friend import (
    FriendItem,
    FriendListResponse,
    FriendRequestListResponse,
    FriendRequestPublic,
    SendFriendRequest,
)
from app.schemas.user import UserPublic
from app.services.push import notify_friend_request
from app.services.friends import (
    AlreadyFriendsError,
    DuplicateFriendRequestError,
    FriendRequestForbiddenError,
    FriendRequestNotFoundError,
    FriendRequestNotPendingError,
    SelfFriendRequestError,
    UserNotFoundError,
    accept_friend_request,
    decline_friend_request,
    list_friends,
    list_incoming_requests,
    list_outgoing_requests,
    send_friend_request,
)

router = APIRouter(prefix="/friends", tags=["friends"])


def _http_error(exc: Exception) -> HTTPException:
    mapping: dict[type[Exception], int] = {
        UserNotFoundError: status.HTTP_404_NOT_FOUND,
        FriendRequestNotFoundError: status.HTTP_404_NOT_FOUND,
        SelfFriendRequestError: status.HTTP_400_BAD_REQUEST,
        DuplicateFriendRequestError: status.HTTP_409_CONFLICT,
        AlreadyFriendsError: status.HTTP_409_CONFLICT,
        FriendRequestNotPendingError: status.HTTP_409_CONFLICT,
        FriendRequestForbiddenError: status.HTTP_403_FORBIDDEN,
    }
    code = mapping.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=code, detail=str(exc))


@router.get("", response_model=FriendListResponse)
def get_friends(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FriendListResponse:
    rows = list_friends(db, current_user)
    return FriendListResponse(
        friends=[
            FriendItem(user=UserPublic.model_validate(user), friends_since=since)
            for user, since in rows
        ]
    )


@router.get("/requests/incoming", response_model=FriendRequestListResponse)
def get_incoming_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FriendRequestListResponse:
    return FriendRequestListResponse(
        requests=[
            FriendRequestPublic.model_validate(row)
            for row in list_incoming_requests(db, current_user)
        ]
    )


@router.get("/requests/outgoing", response_model=FriendRequestListResponse)
def get_outgoing_requests(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FriendRequestListResponse:
    return FriendRequestListResponse(
        requests=[
            FriendRequestPublic.model_validate(row)
            for row in list_outgoing_requests(db, current_user)
        ]
    )


@router.post(
    "/requests",
    response_model=FriendRequestPublic,
    status_code=status.HTTP_201_CREATED,
)
def create_friend_request(
    body: SendFriendRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FriendRequestPublic:
    try:
        request = send_friend_request(db, current_user, body.username)
    except (
        UserNotFoundError,
        SelfFriendRequestError,
        DuplicateFriendRequestError,
        AlreadyFriendsError,
    ) as exc:
        raise _http_error(exc) from exc
    try:
        notify_friend_request(
            db,
            addressee_id=int(request.addressee_id),
            from_name=str(current_user.username),
        )
    except Exception:
        pass
    return FriendRequestPublic.model_validate(request)


@router.post("/requests/{request_id}/accept", response_model=FriendRequestPublic)
def accept_request(
    request_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FriendRequestPublic:
    try:
        request = accept_friend_request(db, current_user, request_id)
    except (
        FriendRequestNotFoundError,
        FriendRequestForbiddenError,
        FriendRequestNotPendingError,
    ) as exc:
        raise _http_error(exc) from exc
    return FriendRequestPublic.model_validate(request)


@router.post("/requests/{request_id}/decline", response_model=FriendRequestPublic)
def decline_request(
    request_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FriendRequestPublic:
    try:
        request = decline_friend_request(db, current_user, request_id)
    except (
        FriendRequestNotFoundError,
        FriendRequestForbiddenError,
        FriendRequestNotPendingError,
    ) as exc:
        raise _http_error(exc) from exc
    return FriendRequestPublic.model_validate(request)
