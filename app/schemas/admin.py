from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.auth import _USERNAME_RE


class AdminUserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    postal_code: str | None = None
    token_balance: int = 0
    is_disabled: bool = False
    is_verified: bool = True
    play_intent: str | None = None
    play_style: str | None = None
    skins_frequency: str | None = None
    skins_feel: str | None = None
    skins_pot_band: str | None = None
    starting_tokens: int | None = None
    created_at: datetime | None = None


class AdminUserListResponse(BaseModel):
    users: list[AdminUserPublic]


class AdminSetBalanceRequest(BaseModel):
    balance: int = Field(ge=0, le=10_000_000)


class AdminRenameRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = value.strip()
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError(
                "Username must be 3–32 characters and use only letters, numbers, and underscores."
            )
        return username


class AdminFlagRequest(BaseModel):
    disabled: bool = True
