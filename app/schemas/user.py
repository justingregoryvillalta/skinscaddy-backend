from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services.signup_profile import parse_profile


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str = Field(min_length=3, max_length=32)
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    postal_code: str | None = None
    token_balance: int = 0
    is_verified: bool = True
    signup_complete: bool = False
    play_intent: str | None = None
    starting_tokens: int | None = None
    tos_version: str | None = None
    tos_accepted_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def pull_signup_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            payload = dict(data)
            profile = parse_profile(payload.get("signup_profile"))
        else:
            payload = {
                "id": getattr(data, "id", None),
                "username": getattr(data, "username", None),
                "first_name": getattr(data, "first_name", None),
                "last_name": getattr(data, "last_name", None),
                "email": getattr(data, "email", None),
                "postal_code": getattr(data, "postal_code", None),
                "token_balance": getattr(data, "token_balance", 0),
                "is_verified": getattr(data, "is_verified", True),
                "tos_version": getattr(data, "tos_version", None),
                "tos_accepted_at": getattr(data, "tos_accepted_at", None),
                "created_at": getattr(data, "created_at", None),
            }
            profile = parse_profile(getattr(data, "signup_profile", None))
        if profile:
            payload["signup_complete"] = True
            payload["play_intent"] = profile.get("play_intent")
            payload["starting_tokens"] = profile.get("starting_tokens")
        return payload


class AcceptTosRequest(BaseModel):
    tos_version: str = Field(min_length=8, max_length=16)

    @field_validator("tos_version")
    @classmethod
    def strip_version(cls, value: str) -> str:
        version = (value or "").strip()
        if not version:
            raise ValueError("tos_version is required.")
        return version


class SignupProfileRequest(BaseModel):
    play_intent: str = Field(min_length=3, max_length=16)
    play_style: str = Field(min_length=3, max_length=16)
    skins_frequency: str = Field(min_length=3, max_length=16)
    skins_feel: str | None = Field(default=None, max_length=16)
    skins_pot_band: str | None = Field(default=None, max_length=16)

    @field_validator("skins_feel", "skins_pot_band")
    @classmethod
    def empty_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class SignupProfileResponse(BaseModel):
    play_intent: str
    play_style: str
    skins_frequency: str
    skins_feel: str | None = None
    skins_pot_band: str | None = None
    starting_tokens: int
    token_balance: int
    credited: int = 0
    skins_topup_done: bool = False
    message: str = ""


class FirstSkinsTopupRequest(BaseModel):
    pot_per_hole: int = Field(ge=1, le=10_000)


class FirstSkinsTopupResponse(BaseModel):
    token_balance: int
    credited: int = 0
    target: int = 0
    applied: bool = False
    message: str = ""
