from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.wallet import (
    CREDIT_SOURCES,
    DEBIT_SOURCES,
    TokenDirection,
    TokenLedger,
    TokenSource,
)

SOURCE_LABELS: dict[TokenSource, str] = {
    TokenSource.REWARD: "Reward",
    TokenSource.WELCOME: "Welcome bonus",
    TokenSource.ROUND_COMPLETE_9: "Completed 9-hole round",
    TokenSource.ROUND_COMPLETE_18: "Completed 18-hole round",
    TokenSource.PAR: "Par",
    TokenSource.BIRDIE: "Birdie",
    TokenSource.EAGLE: "Eagle",
    TokenSource.SKINS_WIN: "Won a skin",
    TokenSource.CHALLENGE_WIN: "Won a challenge",
    TokenSource.WAGER: "Wager",
    TokenSource.FORFEIT: "Forfeit",
    TokenSource.PURCHASE: "Purchase",
    TokenSource.ADJUSTMENT: "Adjustment",
}

# Defaults for later award_tokens() calls from rounds / skins / challenges.
DEFAULT_CREDIT_AMOUNTS: dict[TokenSource, int] = {
    TokenSource.WELCOME: 1000,
    TokenSource.ROUND_COMPLETE_9: 50,
    TokenSource.ROUND_COMPLETE_18: 100,
    TokenSource.PAR: 5,
    TokenSource.BIRDIE: 15,
    TokenSource.EAGLE: 40,
    TokenSource.SKINS_WIN: 25,
    TokenSource.CHALLENGE_WIN: 50,
}


class WalletError(ValueError):
    pass


class InvalidTokenAmountError(WalletError):
    pass


class InvalidTokenSourceError(WalletError):
    pass


class InsufficientTokensError(WalletError):
    pass


def _reason_for(source: TokenSource, reason: str | None) -> str:
    if reason:
        return reason
    return SOURCE_LABELS[source]


def wallet_totals(db: Session, user_id: int) -> tuple[int, int]:
    earned, spent = db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (TokenLedger.direction == TokenDirection.CREDIT, TokenLedger.amount),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (TokenLedger.direction == TokenDirection.DEBIT, TokenLedger.amount),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(TokenLedger.user_id == user_id)
    ).one()
    return int(earned or 0), int(spent or 0)


def get_wallet(db: Session, user: User) -> tuple[int, int, int]:
    earned, spent = wallet_totals(db, user.id)
    return int(user.token_balance), earned, spent


def list_ledger(
    db: Session,
    user: User,
    *,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[TokenLedger], int]:
    total = int(
        db.scalar(
            select(func.count()).select_from(TokenLedger).where(TokenLedger.user_id == user.id)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(TokenLedger)
            .where(TokenLedger.user_id == user.id)
            .order_by(TokenLedger.created_at.desc(), TokenLedger.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return rows, total


def _apply(
    db: Session,
    user: User,
    *,
    direction: TokenDirection,
    amount: int,
    source: TokenSource,
    reason: str | None,
    reference: str | None,
    commit: bool = True,
) -> TokenLedger:
    if amount <= 0:
        raise InvalidTokenAmountError("Amount must be greater than zero.")

    allowed = CREDIT_SOURCES if direction == TokenDirection.CREDIT else DEBIT_SOURCES
    if source not in allowed:
        verb = "credit" if direction == TokenDirection.CREDIT else "debit"
        raise InvalidTokenSourceError(f"Source '{source.value}' cannot be used to {verb} tokens.")

    locked = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked is None:
        raise WalletError("User not found.")

    if direction == TokenDirection.DEBIT:
        if locked.token_balance < amount:
            raise InsufficientTokensError("Insufficient tokens.")
        locked.token_balance -= amount
    else:
        locked.token_balance += amount

    entry = TokenLedger(
        user_id=locked.id,
        direction=direction,
        amount=amount,
        source=source,
        reason=_reason_for(source, reason),
        reference=reference,
        balance_after=locked.token_balance,
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
        db.refresh(locked)
    else:
        db.flush()
    user.token_balance = locked.token_balance
    return entry


def credit_tokens(
    db: Session,
    user: User,
    *,
    amount: int,
    source: TokenSource,
    reason: str | None = None,
    reference: str | None = None,
    commit: bool = True,
) -> TokenLedger:
    return _apply(
        db,
        user,
        direction=TokenDirection.CREDIT,
        amount=amount,
        source=source,
        reason=reason,
        reference=reference,
        commit=commit,
    )


def debit_tokens(
    db: Session,
    user: User,
    *,
    amount: int,
    source: TokenSource,
    reason: str | None = None,
    reference: str | None = None,
    commit: bool = True,
) -> TokenLedger:
    return _apply(
        db,
        user,
        direction=TokenDirection.DEBIT,
        amount=amount,
        source=source,
        reason=reason,
        reference=reference,
        commit=commit,
    )


def award_tokens(
    db: Session,
    user: User,
    source: TokenSource,
    *,
    amount: int | None = None,
    reason: str | None = None,
    reference: str | None = None,
    commit: bool = True,
) -> TokenLedger:
    """Credit helper for future round / skins / challenge rewards."""
    resolved = amount if amount is not None else DEFAULT_CREDIT_AMOUNTS.get(source)
    if resolved is None:
        raise InvalidTokenAmountError("Amount is required for this award source.")
    return credit_tokens(
        db,
        user,
        amount=resolved,
        source=source,
        reason=reason,
        reference=reference,
        commit=commit,
    )
