from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.photo import PhotoKind
from app.models.user import User
from app.schemas.photo import PhotoListResponse, PhotoPublic
from app.services.challenges import ChallengeForbiddenError, ChallengeNotFoundError
from app.services.friends import UserNotFoundError
from app.services.photos import (
    PhotoForbiddenError,
    PhotoGoneError,
    PhotoInvalidError,
    PhotoNotFoundError,
    get_visible_photo,
    list_photos,
    read_photo_bytes,
    to_public,
    upload_photo,
)

router = APIRouter(prefix="/photos", tags=["photos"])


def _http_error(exc: Exception) -> HTTPException:
    mapping: dict[type[Exception], int] = {
        PhotoNotFoundError: status.HTTP_404_NOT_FOUND,
        ChallengeNotFoundError: status.HTTP_404_NOT_FOUND,
        UserNotFoundError: status.HTTP_404_NOT_FOUND,
        PhotoForbiddenError: status.HTTP_403_FORBIDDEN,
        ChallengeForbiddenError: status.HTTP_403_FORBIDDEN,
        PhotoGoneError: status.HTTP_410_GONE,
        PhotoInvalidError: status.HTTP_400_BAD_REQUEST,
    }
    code = mapping.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=code, detail=str(exc))


def _public(photo) -> PhotoPublic:
    return PhotoPublic.model_validate(to_public(photo))


@router.post("", response_model=PhotoPublic, status_code=status.HTTP_201_CREATED)
async def post_photo(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form()],
    challenge_id: Annotated[int | None, Form()] = None,
    recipients: Annotated[str | None, Form()] = None,
    hole: Annotated[int | None, Form()] = None,
    caption: Annotated[str | None, Form()] = None,
    expires_in_days: Annotated[int, Form()] = 7,
) -> PhotoPublic:
    try:
        photo_kind = PhotoKind(kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="kind must be 'challenge' or 'prop'.",
        ) from exc

    data = await file.read()
    names = [part.strip() for part in (recipients or "").split(",") if part.strip()]
    try:
        photo = upload_photo(
            db,
            current_user,
            data=data,
            filename=file.filename,
            declared_type=file.content_type or "",
            kind=photo_kind,
            challenge_id=challenge_id,
            recipient_usernames=names,
            hole=hole,
            caption=caption,
            expires_in_days=expires_in_days,
        )
    except (
        PhotoInvalidError,
        PhotoForbiddenError,
        PhotoNotFoundError,
        ChallengeNotFoundError,
        ChallengeForbiddenError,
        UserNotFoundError,
    ) as exc:
        raise _http_error(exc) from exc
    return _public(photo)


@router.get("", response_model=PhotoListResponse)
def get_photos(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    challenge_id: Annotated[int | None, Query()] = None,
    kind: Annotated[PhotoKind | None, Query()] = None,
) -> PhotoListResponse:
    rows = list_photos(db, current_user, challenge_id=challenge_id, kind=kind)
    return PhotoListResponse(photos=[_public(row) for row in rows])


@router.get("/{photo_id}", response_model=PhotoPublic)
def get_one(
    photo_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> PhotoPublic:
    try:
        photo = get_visible_photo(db, current_user, photo_id)
    except (PhotoNotFoundError, PhotoForbiddenError) as exc:
        raise _http_error(exc) from exc
    return _public(photo)


@router.get("/{photo_id}/file")
def get_file(
    photo_id: int,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> Response:
    try:
        data, content_type, _consumed = read_photo_bytes(db, current_user, photo_id)
    except (PhotoNotFoundError, PhotoForbiddenError, PhotoGoneError) as exc:
        raise _http_error(exc) from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
        },
    )
