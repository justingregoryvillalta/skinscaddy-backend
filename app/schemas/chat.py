from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user import UserPublic


class ChatMemberPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: UserPublic
    joined_at: datetime


class ChatMessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    text: str
    round_id: int | None = None
    snapshot: dict[str, Any] | None = None
    created_at: datetime
    user: UserPublic


class ChatThreadPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    title: str
    created_at: datetime
    updated_at: datetime
    members: list[ChatMemberPublic] = Field(default_factory=list)
    last_message: ChatMessagePublic | None = None


class ChatThreadListResponse(BaseModel):
    threads: list[ChatThreadPublic]


class ChatMessageListResponse(BaseModel):
    messages: list[ChatMessagePublic]


class CreateDirectChatRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        username = (value or "").strip().lstrip("@")
        if len(username) < 3:
            raise ValueError("Username is required.")
        return username


class CreateGroupChatRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    usernames: list[str] = Field(default_factory=list, max_length=24)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        title = " ".join((value or "").split())
        if not title:
            raise ValueError("Group name is required.")
        return title

    @field_validator("usernames")
    @classmethod
    def clean_names(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for raw in value or []:
            name = (raw or "").strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(name)
        return out


class AddChatMemberRequest(BaseModel):
    username: str = Field(min_length=3, max_length=32)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        username = (value or "").strip().lstrip("@")
        if len(username) < 3:
            raise ValueError("Username is required.")
        return username


class SendChatMessageRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    kind: str = Field(default="text", max_length=16)
    round_id: int | None = None
    snapshot: dict[str, Any] | None = None

    @field_validator("kind")
    @classmethod
    def normalize_kind(cls, value: str) -> str:
        kind = (value or "text").strip().lower()
        if kind not in {"text", "round"}:
            raise ValueError("kind must be text or round.")
        return kind

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None

    @model_validator(mode="after")
    def require_body(self) -> SendChatMessageRequest:
        if self.kind == "text" and not self.text:
            raise ValueError("Type a message first.")
        if self.kind == "round" and not self.snapshot and self.round_id is None:
            raise ValueError("Attach a round to share.")
        return self
