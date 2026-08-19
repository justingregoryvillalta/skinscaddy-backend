from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.wallet import TokenDirection, TokenSource


class WalletMutation(BaseModel):
    amount: int = Field(gt=0, le=1_000_000)
    source: TokenSource
    reason: str | None = Field(default=None, max_length=200)
    reference: str | None = Field(default=None, max_length=64)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("reference")
    @classmethod
    def strip_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class LedgerEntryPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    direction: TokenDirection
    amount: int
    source: TokenSource
    reason: str
    reference: str | None
    balance_after: int
    created_at: datetime


class WalletResponse(BaseModel):
    balance: int
    earned: int
    spent: int


class WalletHistoryResponse(BaseModel):
    history: list[LedgerEntryPublic]
    total: int


class WalletMutationResponse(BaseModel):
    balance: int
    earned: int
    spent: int
    entry: LedgerEntryPublic
