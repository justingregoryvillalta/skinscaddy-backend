from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.photo import PhotoKind, PhotoStatus
from app.schemas.user import UserPublic


class PhotoRecipientPublic(BaseModel):
    user: UserPublic
    viewed_at: datetime | None


class PhotoPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: PhotoKind
    status: PhotoStatus
    url: str
    available: bool
    view_once: bool = True
    challenge_id: int | None
    hole: int | None
    caption: str | None
    content_type: str
    byte_size: int
    expires_in_days: int
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime
    sender: UserPublic
    recipients: list[PhotoRecipientPublic]


class PhotoListResponse(BaseModel):
    photos: list[PhotoPublic]


class PhotoUploadMeta(BaseModel):
    kind: PhotoKind
    challenge_id: int | None = None
    recipients: list[str] = Field(default_factory=list)
    hole: int | None = Field(default=None, ge=1, le=18)
    caption: str | None = Field(default=None, max_length=200)
    expires_in_days: int = 7
