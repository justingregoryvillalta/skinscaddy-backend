from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.scramble import (
    CreateScrambleRequest,
    JoinScrambleRequest,
    PostScrambleScoreRequest,
    ScrambleListResponse,
    ScramblePreviewPublic,
    ScrambleStatePublic,
)
from app.services.scrambles import (
    ScrambleForbiddenError,
    ScrambleNotFoundError,
    ScrambleStateError,
    create_scramble,
    get_scramble_by_code,
    get_visible_scramble,
    join_scramble,
    list_my_scrambles,
    post_score,
    preview_state,
    viewer_state,
)

router = APIRouter(prefix="/scrambles", tags=["scrambles"])


def _http_error(exc: Exception) -> HTTPException:
    mapping: dict[type[Exception], int] = {
        ScrambleNotFoundError: status.HTTP_404_NOT_FOUND,
        ScrambleForbiddenError: status.HTTP_403_FORBIDDEN,
        ScrambleStateError: status.HTTP_409_CONFLICT,
    }
    code = mapping.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=code, detail=str(exc))


def _state(scramble, actor: User) -> ScrambleStatePublic:
    return ScrambleStatePublic.model_validate(viewer_state(scramble, actor))


@router.post("", response_model=ScrambleStatePublic, status_code=status.HTTP_201_CREATED)
def post_scramble(
    body: CreateScrambleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScrambleStatePublic:
    try:
        scramble = create_scramble(
            db,
            current_user,
            course_name=body.course_name,
            course_id=body.course_id,
            num_holes=body.num_holes,
            wager_amount=body.wager_amount,
            teams=body.teams,
            host_team_index=body.host_team_index,
            pars=body.pars,
        )
    except ScrambleStateError as exc:
        raise _http_error(exc) from exc
    return _state(scramble, current_user)


@router.post("/join", response_model=ScrambleStatePublic)
def join(
    body: JoinScrambleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScrambleStatePublic:
    try:
        scramble = join_scramble(db, current_user, body.code, body.team_index)
    except (ScrambleNotFoundError, ScrambleStateError) as exc:
        raise _http_error(exc) from exc
    return _state(scramble, current_user)


@router.get("", response_model=ScrambleListResponse)
def list_mine(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScrambleListResponse:
    rows = list_my_scrambles(db, current_user)
    return ScrambleListResponse(scrambles=[_state(row, current_user) for row in rows])


@router.get("/by-code/{code}")
def get_by_code(
    code: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScrambleStatePublic | ScramblePreviewPublic:
    scramble = get_scramble_by_code(db, code)
    if scramble is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No scramble found for that join code.")
    member_ids = {m.user_id for m in scramble.members}
    if current_user.id in member_ids:
        return _state(scramble, current_user)
    return ScramblePreviewPublic.model_validate(preview_state(scramble))


@router.get("/{scramble_id}", response_model=ScrambleStatePublic)
def get_one(
    scramble_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScrambleStatePublic:
    try:
        scramble = get_visible_scramble(db, current_user, scramble_id)
    except (ScrambleNotFoundError, ScrambleForbiddenError) as exc:
        raise _http_error(exc) from exc
    return _state(scramble, current_user)


@router.post("/{scramble_id}/scores", response_model=ScrambleStatePublic)
def post_team_score(
    scramble_id: int,
    body: PostScrambleScoreRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> ScrambleStatePublic:
    try:
        scramble = post_score(
            db,
            current_user,
            scramble_id,
            strokes=body.strokes,
            hole=body.hole,
        )
    except (
        ScrambleNotFoundError,
        ScrambleForbiddenError,
        ScrambleStateError,
    ) as exc:
        raise _http_error(exc) from exc
    return _state(scramble, current_user)
