from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.challenge import Challenge
from app.models.user import User
from app.schemas.challenge import (
    ChallengeListResponse,
    ChallengePublic,
    CreateChallengeRequest,
    JoinRoundRequest,
    SubmitChallengeScoresRequest,
)
from app.services.challenges import (
    ChallengeForbiddenError,
    ChallengeNotFoundError,
    ChallengeStateError,
    NotFriendsError,
    accept_challenge,
    create_challenge,
    decline_challenge,
    get_visible_challenge,
    join_round_challenge,
    list_history,
    list_incoming,
    list_outgoing,
    settle_challenge,
    submit_scores,
)
from app.services.friends import UserNotFoundError
from app.services.push import notify_challenge_event
from app.services.rounds import RoundForbiddenError, RoundNotFoundError
from app.services.wallet import InsufficientTokensError

router = APIRouter(prefix="/challenges", tags=["challenges"])


def _http_error(exc: Exception) -> HTTPException:
    mapping: dict[type[Exception], int] = {
        UserNotFoundError: status.HTTP_404_NOT_FOUND,
        ChallengeNotFoundError: status.HTTP_404_NOT_FOUND,
        RoundNotFoundError: status.HTTP_404_NOT_FOUND,
        NotFriendsError: status.HTTP_403_FORBIDDEN,
        ChallengeForbiddenError: status.HTTP_403_FORBIDDEN,
        RoundForbiddenError: status.HTTP_403_FORBIDDEN,
        ChallengeStateError: status.HTTP_409_CONFLICT,
        InsufficientTokensError: status.HTTP_409_CONFLICT,
    }
    code = mapping.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=code, detail=str(exc))


def _public(challenge: Challenge) -> ChallengePublic:
    return ChallengePublic.model_validate(challenge)


@router.get("", response_model=ChallengeListResponse)
def get_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengeListResponse:
    return ChallengeListResponse(
        challenges=[_public(row) for row in list_history(db, current_user)]
    )


@router.get("/incoming", response_model=ChallengeListResponse)
def get_incoming(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengeListResponse:
    return ChallengeListResponse(
        challenges=[_public(row) for row in list_incoming(db, current_user)]
    )


@router.get("/outgoing", response_model=ChallengeListResponse)
def get_outgoing(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengeListResponse:
    return ChallengeListResponse(
        challenges=[_public(row) for row in list_outgoing(db, current_user)]
    )


@router.post("", response_model=ChallengePublic, status_code=status.HTTP_201_CREATED)
def post_challenge(
    body: CreateChallengeRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = create_challenge(
            db,
            current_user,
            usernames=body.usernames,
            round_id=body.round_id,
            wager_amount=body.wager_amount,
            weeks=body.weeks,
        )
    except (
        UserNotFoundError,
        NotFriendsError,
        ChallengeStateError,
        RoundNotFoundError,
        RoundForbiddenError,
        InsufficientTokensError,
    ) as exc:
        raise _http_error(exc) from exc
    try:
        opp_ids = [
            int(p.user_id)
            for p in challenge.players
            if int(p.user_id) != int(current_user.id)
        ]
        notify_challenge_event(
            db,
            recipient_ids=opp_ids,
            title="Challenge",
            body=f"@{current_user.username} challenged you",
            challenge_id=int(challenge.id),
        )
    except Exception:
        pass
    return _public(challenge)


@router.post("/join-round", response_model=ChallengePublic, status_code=status.HTTP_201_CREATED)
def join_round(
    body: JoinRoundRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = join_round_challenge(
            db,
            current_user,
            round_id=body.round_id,
            wager_amount=body.wager_amount,
            weeks=body.weeks,
        )
    except (
        UserNotFoundError,
        NotFriendsError,
        ChallengeStateError,
        RoundNotFoundError,
        RoundForbiddenError,
        InsufficientTokensError,
    ) as exc:
        raise _http_error(exc) from exc
    try:
        host_id = int(challenge.creator_id)
        if host_id != int(current_user.id):
            notify_challenge_event(
                db,
                recipient_ids=[host_id],
                title="Side game",
                body=f"@{current_user.username} joined your round",
                challenge_id=int(challenge.id),
            )
    except Exception:
        pass
    return _public(challenge)


@router.get("/{challenge_id}", response_model=ChallengePublic)
def get_one(
    challenge_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = get_visible_challenge(db, current_user, challenge_id)
    except (ChallengeNotFoundError, ChallengeForbiddenError) as exc:
        raise _http_error(exc) from exc
    return _public(challenge)


@router.post("/{challenge_id}/accept", response_model=ChallengePublic)
def accept(
    challenge_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = accept_challenge(db, current_user, challenge_id)
    except (
        ChallengeNotFoundError,
        ChallengeForbiddenError,
        ChallengeStateError,
        InsufficientTokensError,
    ) as exc:
        raise _http_error(exc) from exc
    try:
        others = [
            int(p.user_id)
            for p in challenge.players
            if int(p.user_id) != int(current_user.id)
        ]
        notify_challenge_event(
            db,
            recipient_ids=others,
            title="Side game",
            body=f"@{current_user.username} accepted",
            challenge_id=int(challenge.id),
        )
    except Exception:
        pass
    return _public(challenge)


@router.post("/{challenge_id}/decline", response_model=ChallengePublic)
def decline(
    challenge_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = decline_challenge(db, current_user, challenge_id)
    except (
        ChallengeNotFoundError,
        ChallengeForbiddenError,
        ChallengeStateError,
    ) as exc:
        raise _http_error(exc) from exc
    try:
        others = [
            int(p.user_id)
            for p in challenge.players
            if int(p.user_id) != int(current_user.id)
        ]
        notify_challenge_event(
            db,
            recipient_ids=others,
            title="Side game",
            body=f"@{current_user.username} declined",
            challenge_id=int(challenge.id),
        )
    except Exception:
        pass
    return _public(challenge)


@router.post("/{challenge_id}/scores", response_model=ChallengePublic)
def post_scores(
    challenge_id: int,
    body: SubmitChallengeScoresRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = submit_scores(
            db,
            current_user,
            challenge_id,
            strokes=body.strokes,
            scores=body.scores,
        )
    except (
        ChallengeNotFoundError,
        ChallengeForbiddenError,
        ChallengeStateError,
    ) as exc:
        raise _http_error(exc) from exc
    return _public(challenge)


@router.post("/{challenge_id}/settle", response_model=ChallengePublic)
def settle(
    challenge_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ChallengePublic:
    try:
        challenge = settle_challenge(db, current_user, challenge_id)
    except (ChallengeNotFoundError, ChallengeForbiddenError) as exc:
        raise _http_error(exc) from exc
    try:
        others = [
            int(p.user_id)
            for p in challenge.players
            if int(p.user_id) != int(current_user.id)
        ]
        notify_challenge_event(
            db,
            recipient_ids=others,
            title="Side game",
            body=f"@{current_user.username} settled a side game",
            challenge_id=int(challenge.id),
        )
    except Exception:
        pass
    return _public(challenge)
