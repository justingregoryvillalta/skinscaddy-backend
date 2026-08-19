from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.friend import FriendRequestStatus
from app.schemas.user import UserPublic


class SendFriendRequest(BaseModel):
    username: str = Field(min_length=1, max_length=32)

    @field_validator("username")
    @classmethod
    def strip_username(cls, value: str) -> str:
        username = value.strip()
        if not username:
            raise ValueError("Username is required.")
        return username


class FriendRequestPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: FriendRequestStatus
    created_at: datetime
    updated_at: datetime
    requester: UserPublic
    addressee: UserPublic


class FriendItem(BaseModel):
    user: UserPublic
    friends_since: datetime


class FriendListResponse(BaseModel):
    friends: list[FriendItem]


class FriendRequestListResponse(BaseModel):
    requests: list[FriendRequestPublic]
