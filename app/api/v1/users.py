from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.user import UserPublic

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserPublic)
def read_me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user


@router.get("/protected")
def protected_example(current_user: Annotated[User, Depends(get_current_user)]) -> dict:
    return {
        "ok": True,
        "message": "Token is valid.",
        "user": UserPublic.model_validate(current_user).model_dump(mode="json"),
    }
