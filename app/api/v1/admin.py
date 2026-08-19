from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user
from app.config import get_settings
from app.database import get_db
from app.models.user import User
from app.models.wallet import TokenSource
from app.schemas.admin import (
    AdminFlagRequest,
    AdminRenameRequest,
    AdminSetBalanceRequest,
    AdminUserListResponse,
    AdminUserPublic,
)
from app.services.users import (
    AdminError,
    UsernameTakenError,
    delete_user,
    get_user_by_id,
    list_users,
    rename_user,
    set_user_disabled,
)
from app.services.wallet import credit_tokens, debit_tokens

router = APIRouter(prefix="/admin", tags=["admin"])


def _admin_name() -> str:
    return (get_settings().ADMIN_USERNAME or "justinv").strip().lower()


def _public(user: User) -> AdminUserPublic:
    return AdminUserPublic.model_validate(user)


def _require_target(db: Session, user_id: int) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


def _guard_admin_account(user: User, *, action: str) -> None:
    if user.username.lower() == _admin_name():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot {action} the admin account.",
        )


@router.get("/users", response_model=AdminUserListResponse)
def admin_list_users(
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserListResponse:
    return AdminUserListResponse(users=[_public(user) for user in list_users(db)])


@router.put("/users/{user_id}/tokens", response_model=AdminUserPublic)
def admin_set_tokens(
    user_id: int,
    body: AdminSetBalanceRequest,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserPublic:
    user = _require_target(db, user_id)
    current = int(user.token_balance or 0)
    target = int(body.balance)
    delta = target - current
    if delta > 0:
        credit_tokens(
            db,
            user,
            amount=delta,
            source=TokenSource.ADJUSTMENT,
            reason="Admin adjustment",
            reference=f"admin:{admin.id}",
        )
    elif delta < 0:
        debit_tokens(
            db,
            user,
            amount=-delta,
            source=TokenSource.ADJUSTMENT,
            reason="Admin adjustment",
            reference=f"admin:{admin.id}",
        )
    db.refresh(user)
    return _public(user)


@router.put("/users/{user_id}/username", response_model=AdminUserPublic)
def admin_rename_user(
    user_id: int,
    body: AdminRenameRequest,
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserPublic:
    user = _require_target(db, user_id)
    _guard_admin_account(user, action="rename")
    try:
        user = rename_user(db, user, body.username)
    except UsernameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _public(user)


@router.post("/users/{user_id}/flag", response_model=AdminUserPublic)
def admin_flag_user(
    user_id: int,
    body: AdminFlagRequest,
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserPublic:
    user = _require_target(db, user_id)
    _guard_admin_account(user, action="flag")
    return _public(set_user_disabled(db, user, body.disabled))


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def admin_delete_user(
    user_id: int,
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    user = _require_target(db, user_id)
    _guard_admin_account(user, action="delete")
    try:
        delete_user(db, user)
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
