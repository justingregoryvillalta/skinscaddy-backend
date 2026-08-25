from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user
from app.database import get_db
from app.models.user import User
from app.schemas.admin import (
    AdminFlagRequest,
    AdminRenameRequest,
    AdminSetBalanceRequest,
    AdminUserListResponse,
    AdminUserPublic,
)
from app.services.signup_profile import parse_profile
from app.services.users import (
    AdminError,
    UsernameTakenError,
    delete_user,
    get_user_by_id,
    get_user_row,
    grant_missing_welcome_bonuses,
    list_user_rows,
    rename_user_by_id,
    set_user_disabled,
)
from app.services.wallet import WalletError, set_token_balance

router = APIRouter(prefix="/admin", tags=["admin"])


def _profile_fields(raw: Any) -> dict[str, Any]:
    profile = parse_profile(raw) or {}
    start = profile.get("starting_tokens")
    try:
        start_n = int(start) if start is not None else None
    except (TypeError, ValueError):
        start_n = None
    return {
        "play_intent": profile.get("play_intent"),
        "play_style": profile.get("play_style"),
        "skins_frequency": profile.get("skins_frequency"),
        "skins_feel": profile.get("skins_feel"),
        "skins_pot_band": profile.get("skins_pot_band"),
        "starting_tokens": start_n,
    }


def _public(row: User | dict[str, Any]) -> AdminUserPublic:
    if isinstance(row, dict):
        extra = _profile_fields(row.get("signup_profile"))
        return AdminUserPublic(
            id=int(row["id"]),
            username=str(row["username"]),
            first_name=row.get("first_name"),
            last_name=row.get("last_name"),
            email=row.get("email"),
            postal_code=row.get("postal_code"),
            token_balance=int(row.get("token_balance") or 0),
            is_disabled=bool(row.get("is_disabled")),
            is_verified=bool(row["is_verified"]) if row.get("is_verified") is not None else True,
            created_at=row.get("created_at"),
            **extra,
        )
    extra = _profile_fields(getattr(row, "signup_profile", None))
    return AdminUserPublic(
        id=int(row.id),
        username=str(row.username),
        first_name=getattr(row, "first_name", None),
        last_name=getattr(row, "last_name", None),
        email=getattr(row, "email", None),
        postal_code=getattr(row, "postal_code", None),
        token_balance=int(getattr(row, "token_balance", 0) or 0),
        is_disabled=bool(getattr(row, "is_disabled", False)),
        is_verified=bool(getattr(row, "is_verified", True)),
        created_at=getattr(row, "created_at", None),
        **extra,
    )


def _require_row(db: Session, user_id: int) -> dict[str, Any]:
    row = get_user_row(db, user_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return row


def _require_target(db: Session, user_id: int) -> User:
    user = get_user_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.get("/users", response_model=AdminUserListResponse)
def admin_list_users(
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserListResponse:
    try:
        grant_missing_welcome_bonuses(db)
    except Exception:
        db.rollback()
    rows = list_user_rows(db)
    return AdminUserListResponse(users=[_public(row) for row in rows])


@router.put("/users/{user_id}/tokens", response_model=AdminUserPublic)
def admin_set_tokens(
    user_id: int,
    body: AdminSetBalanceRequest,
    admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserPublic:
    # justinv may edit their own balance — no self-exclusion.
    _require_row(db, user_id)
    try:
        row = set_token_balance(
            db,
            user_id,
            int(body.balance),
            reason="Admin adjustment",
            reference=f"admin:{admin.id}",
        )
    except WalletError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _public(row)


@router.put("/users/{user_id}/username", response_model=AdminUserPublic)
def admin_rename_user(
    user_id: int,
    body: AdminRenameRequest,
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserPublic:
    # justinv may rename their own account — no self-exclusion.
    _require_row(db, user_id)
    try:
        row = rename_user_by_id(db, user_id, body.username)
    except UsernameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except AdminError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if "not found" in str(exc).lower()
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    return _public(row)


@router.post("/users/{user_id}/flag", response_model=AdminUserPublic)
def admin_flag_user(
    user_id: int,
    body: AdminFlagRequest,
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> AdminUserPublic:
    user = _require_target(db, user_id)
    try:
        return _public(set_user_disabled(db, user, body.disabled))
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def admin_delete_user(
    user_id: int,
    _admin: Annotated[User, Depends(get_admin_user)],
    db: Annotated[Session, Depends(get_db)],
) -> None:
    user = _require_target(db, user_id)
    try:
        delete_user(db, user)
    except AdminError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
