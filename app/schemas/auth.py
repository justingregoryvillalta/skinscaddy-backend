from __future__ import annotations

import re

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.user import UserPublic

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z '\-]{0,48}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_POSTAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-]{1,14}$")


def _clean_name(value: str, *, label: str) -> str:
    name = " ".join((value or "").split())
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"{label} must be 1–50 letters (spaces, hyphen, and apostrophe allowed)."
        )
    return name


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)
    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    email: str = Field(min_length=5, max_length=254)
    postal_code: str = Field(min_length=3, max_length=16)
    accept_tos: bool = False
    tos_version: str | None = Field(default=None, max_length=16)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        username = value.strip()
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError(
                "Username must be 3–32 characters and use only letters, numbers, and underscores."
            )
        return username

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("Password cannot start or end with spaces.")
        if len(value.strip()) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return value

    @field_validator("first_name")
    @classmethod
    def validate_first_name(cls, value: str) -> str:
        return _clean_name(value, label="First name")

    @field_validator("last_name")
    @classmethod
    def validate_last_name(cls, value: str) -> str:
        return _clean_name(value, label="Last name")

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        email = (value or "").strip().lower()
        if not _EMAIL_RE.fullmatch(email):
            raise ValueError("Enter a valid email address.")
        return email

    @field_validator("postal_code")
    @classmethod
    def validate_postal(cls, value: str) -> str:
        postal = " ".join((value or "").strip().upper().split())
        if not _POSTAL_RE.fullmatch(postal):
            raise ValueError("Enter a valid postal or ZIP code.")
        return postal

    @field_validator("tos_version")
    @classmethod
    def empty_tos_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def require_tos(self) -> "RegisterRequest":
        if not self.accept_tos:
            raise ValueError("You must agree to Terms and Privacy.")
        return self

    # Optional quiz answers — credited on the new account without logging in.
    play_intent: str | None = Field(default=None, max_length=16)
    play_style: str | None = Field(default=None, max_length=16)
    skins_frequency: str | None = Field(default=None, max_length=16)
    skins_feel: str | None = Field(default=None, max_length=16)
    skins_pot_band: str | None = Field(default=None, max_length=16)

    @field_validator(
        "play_intent",
        "play_style",
        "skins_frequency",
        "skins_feel",
        "skins_pot_band",
    )
    @classmethod
    def empty_quiz_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class RegisterResponse(BaseModel):
    ok: bool = True
    username: str
    email: str
    message: str
    email_sent: bool = False
    email_error: str | None = None
    verification_url: str | None = None
    verification_token: str | None = None
    starting_tokens: int | None = None


class VerifyRequest(BaseModel):
    token: str = Field(min_length=8, max_length=200)


class ResendVerificationRequest(BaseModel):
    email: str | None = None
    username: str | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        email = value.strip().lower()
        return email or None

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        name = value.strip()
        return name or None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=72)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        return value.strip()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic
