from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.wallet import (
    LedgerEntryPublic,
    WalletHistoryResponse,
    WalletMutation,
    WalletMutationResponse,
    WalletResponse,
)
from app.services.wallet import (
    InsufficientTokensError,
    InvalidTokenAmountError,
    InvalidTokenSourceError,
    credit_tokens,
    debit_tokens,
    get_wallet,
    list_ledger,
)

router = APIRouter(prefix="/wallet", tags=["wallet"])


def _http_error(exc: Exception) -> HTTPException:
    mapping: dict[type[Exception], int] = {
        InvalidTokenAmountError: status.HTTP_400_BAD_REQUEST,
        InvalidTokenSourceError: status.HTTP_400_BAD_REQUEST,
        InsufficientTokensError: status.HTTP_409_CONFLICT,
    }
    code = mapping.get(type(exc), status.HTTP_400_BAD_REQUEST)
    return HTTPException(status_code=code, detail=str(exc))


def _wallet_body(db: Session, user: User) -> dict:
    balance, earned, spent = get_wallet(db, user)
    return {"balance": balance, "earned": earned, "spent": spent}


@router.get("", response_model=WalletResponse)
def read_wallet(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WalletResponse:
    return WalletResponse(**_wallet_body(db, current_user))


@router.get("/history", response_model=WalletHistoryResponse)
def read_history(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WalletHistoryResponse:
    rows, total = list_ledger(db, current_user, limit=limit, offset=offset)
    return WalletHistoryResponse(
        history=[LedgerEntryPublic.model_validate(row) for row in rows],
        total=total,
    )


@router.post("/credit", response_model=WalletMutationResponse, status_code=status.HTTP_201_CREATED)
def credit_wallet(
    body: WalletMutation,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WalletMutationResponse:
    try:
        entry = credit_tokens(
            db,
            current_user,
            amount=body.amount,
            source=body.source,
            reason=body.reason,
            reference=body.reference,
        )
    except (InvalidTokenAmountError, InvalidTokenSourceError) as exc:
        raise _http_error(exc) from exc
    return WalletMutationResponse(
        **_wallet_body(db, current_user),
        entry=LedgerEntryPublic.model_validate(entry),
    )


@router.post("/debit", response_model=WalletMutationResponse)
def debit_wallet(
    body: WalletMutation,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> WalletMutationResponse:
    try:
        entry = debit_tokens(
            db,
            current_user,
            amount=body.amount,
            source=body.source,
            reason=body.reason,
            reference=body.reference,
        )
    except (InvalidTokenAmountError, InvalidTokenSourceError, InsufficientTokensError) as exc:
        raise _http_error(exc) from exc
    return WalletMutationResponse(
        **_wallet_body(db, current_user),
        entry=LedgerEntryPublic.model_validate(entry),
    )
