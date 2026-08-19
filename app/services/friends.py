from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models.friend import FriendRequest, FriendRequestStatus
from app.models.user import User
from app.services.users import get_user_by_username


class FriendError(ValueError):
    pass


class UserNotFoundError(FriendError):
    pass


class SelfFriendRequestError(FriendError):
    pass


class DuplicateFriendRequestError(FriendError):
    pass


class AlreadyFriendsError(FriendError):
    pass


class FriendRequestNotFoundError(FriendError):
    pass


class FriendRequestForbiddenError(FriendError):
    pass


class FriendRequestNotPendingError(FriendError):
    pass


_REQUEST_LOAD = (
    selectinload(FriendRequest.requester),
    selectinload(FriendRequest.addressee),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_request(db: Session, request_id: int) -> FriendRequest | None:
    return db.scalar(
        select(FriendRequest).options(*_REQUEST_LOAD).where(FriendRequest.id == request_id)
    )


def _pair_requests(db: Session, user_a_id: int, user_b_id: int) -> list[FriendRequest]:
    return list(
        db.scalars(
            select(FriendRequest)
            .options(*_REQUEST_LOAD)
            .where(
                or_(
                    and_(
                        FriendRequest.requester_id == user_a_id,
                        FriendRequest.addressee_id == user_b_id,
                    ),
                    and_(
                        FriendRequest.requester_id == user_b_id,
                        FriendRequest.addressee_id == user_a_id,
                    ),
                )
            )
        ).all()
    )


def send_friend_request(db: Session, actor: User, username: str) -> FriendRequest:
    target = get_user_by_username(db, username)
    if target is None:
        raise UserNotFoundError("User not found.")
    if target.id == actor.id:
        raise SelfFriendRequestError("You cannot send a friend request to yourself.")

    existing = _pair_requests(db, actor.id, target.id)
    if any(row.status == FriendRequestStatus.ACCEPTED for row in existing):
        raise AlreadyFriendsError("You are already friends.")
    if any(row.status == FriendRequestStatus.PENDING for row in existing):
        raise DuplicateFriendRequestError("A friend request already exists.")

    declined = next(
        (
            row
            for row in existing
            if row.requester_id == actor.id and row.status == FriendRequestStatus.DECLINED
        ),
        None,
    )
    if declined is not None:
        declined.status = FriendRequestStatus.PENDING
        declined.created_at = _now()
        declined.updated_at = _now()
        db.commit()
        db.refresh(declined)
        return declined

    request = FriendRequest(
        requester_id=actor.id,
        addressee_id=target.id,
        status=FriendRequestStatus.PENDING,
    )
    db.add(request)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateFriendRequestError("A friend request already exists.") from exc
    db.refresh(request)
    return _get_request(db, request.id) or request


def _require_pending_incoming(db: Session, actor: User, request_id: int) -> FriendRequest:
    request = _get_request(db, request_id)
    if request is None:
        raise FriendRequestNotFoundError("Friend request not found.")
    if request.addressee_id != actor.id:
        raise FriendRequestForbiddenError("You cannot respond to this friend request.")
    if request.status != FriendRequestStatus.PENDING:
        raise FriendRequestNotPendingError("Friend request is no longer pending.")
    return request


def accept_friend_request(db: Session, actor: User, request_id: int) -> FriendRequest:
    request = _require_pending_incoming(db, actor, request_id)
    now = _now()
    request.status = FriendRequestStatus.ACCEPTED
    request.updated_at = now

    reverse = db.scalar(
        select(FriendRequest).where(
            FriendRequest.requester_id == request.addressee_id,
            FriendRequest.addressee_id == request.requester_id,
        )
    )
    if reverse is not None and reverse.status != FriendRequestStatus.ACCEPTED:
        reverse.status = FriendRequestStatus.DECLINED
        reverse.updated_at = now

    db.commit()
    db.refresh(request)
    return _get_request(db, request.id) or request


def decline_friend_request(db: Session, actor: User, request_id: int) -> FriendRequest:
    request = _require_pending_incoming(db, actor, request_id)
    request.status = FriendRequestStatus.DECLINED
    request.updated_at = _now()
    db.commit()
    db.refresh(request)
    return _get_request(db, request.id) or request


def are_friends(db: Session, user_a_id: int, user_b_id: int) -> bool:
    if user_a_id == user_b_id:
        return False
    return any(row.status == FriendRequestStatus.ACCEPTED for row in _pair_requests(db, user_a_id, user_b_id))


def list_friend_ids(db: Session, actor: User) -> list[int]:
    return [user.id for user, _ in list_friends(db, actor)]


def list_friends(db: Session, actor: User) -> list[tuple[User, datetime]]:
    rows = db.scalars(
        select(FriendRequest)
        .options(*_REQUEST_LOAD)
        .where(
            FriendRequest.status == FriendRequestStatus.ACCEPTED,
            or_(
                FriendRequest.requester_id == actor.id,
                FriendRequest.addressee_id == actor.id,
            ),
        )
        .order_by(FriendRequest.updated_at.desc())
    ).all()

    friends: list[tuple[User, datetime]] = []
    for row in rows:
        other = row.addressee if row.requester_id == actor.id else row.requester
        friends.append((other, row.updated_at))
    return friends


def list_incoming_requests(db: Session, actor: User) -> list[FriendRequest]:
    return list(
        db.scalars(
            select(FriendRequest)
            .options(*_REQUEST_LOAD)
            .where(
                FriendRequest.addressee_id == actor.id,
                FriendRequest.status == FriendRequestStatus.PENDING,
            )
            .order_by(FriendRequest.created_at.desc())
        ).all()
    )


def list_outgoing_requests(db: Session, actor: User) -> list[FriendRequest]:
    return list(
        db.scalars(
            select(FriendRequest)
            .options(*_REQUEST_LOAD)
            .where(
                FriendRequest.requester_id == actor.id,
                FriendRequest.status == FriendRequestStatus.PENDING,
            )
            .order_by(FriendRequest.created_at.desc())
        ).all()
    )
