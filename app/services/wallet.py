from __future__ import annotations

from sqlalchemy import case, func, select, text
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
WELCOME_BONUS = 100

DEFAULT_CREDIT_AMOUNTS: dict[TokenSource, int] = {
    TokenSource.WELCOME: WELCOME_BONUS,
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

    # Avoid SELECT ... FOR UPDATE. It is a no-op or error on some SQLite
    # builds and can fail when the same session already holds the admin row.
    locked = db.get(User, user.id)
    if locked is None:
        locked = db.scalar(select(User).where(User.id == user.id))
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


def set_token_balance(
    db: Session,
    user_id: int,
    balance: int,
    *,
    reason: str = "Admin adjustment",
    reference: str | None = None,
) -> dict:
    """Set any user's token_balance, including justinv. Raw UPDATE + ledger."""
    if balance < 0:
        raise InvalidTokenAmountError("Balance cannot be negative.")

    row = db.execute(
        text(
            "SELECT id, username, token_balance FROM users WHERE id = :uid"
        ),
        {"uid": int(user_id)},
    ).mappings().first()
    if row is None:
        raise WalletError("User not found.")

    current = int(row["token_balance"] or 0)
    target = int(balance)
    delta = target - current
    if delta != 0:
        db.execute(
            text("UPDATE users SET token_balance = :balance WHERE id = :uid"),
            {"balance": target, "uid": int(user_id)},
        )
        direction = TokenDirection.CREDIT if delta > 0 else TokenDirection.DEBIT
        db.add(
            TokenLedger(
                user_id=int(user_id),
                direction=direction,
                amount=abs(delta),
                source=TokenSource.ADJUSTMENT,
                reason=reason or SOURCE_LABELS[TokenSource.ADJUSTMENT],
                reference=reference,
                balance_after=target,
            )
        )
        db.commit()
        cached = db.get(User, int(user_id))
        if cached is not None:
            cached.token_balance = target

    created = None
    try:
        created_row = db.execute(
            text("SELECT created_at, is_disabled FROM users WHERE id = :uid"),
            {"uid": int(user_id)},
        ).mappings().first()
    except Exception:
        db.rollback()
        created_row = None
    if created_row is not None:
        created = created_row.get("created_at")
        disabled = bool(created_row.get("is_disabled"))
    else:
        disabled = False

    return {
        "id": int(row["id"]),
        "username": str(row["username"]),
        "token_balance": target,
        "is_disabled": disabled,
        "created_at": created,
    }
