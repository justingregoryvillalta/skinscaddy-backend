from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.status import ActivityKind, LiveState, PlayMode, PrivacyMode
from app.schemas.user import UserPublic

MODE_LABELS = {
    PlayMode.SOLO: "Solo 2.0",
    PlayMode.SKINS: "Skins",
    PlayMode.SCRAMBLE: "Scramble",
}


class UpdateStatusRequest(BaseModel):
    state: LiveState
    mode: PlayMode | None = None
    course_name: str | None = Field(default=None, max_length=120)
    course_id: str | None = Field(default=None, max_length=64)
    hole: int | None = Field(default=None, ge=1, le=18)
    holes_completed: int | None = Field(default=None, ge=0, le=18)
    num_holes: int | None = Field(default=None)
    scores: list[int] | None = None
    total: int | None = Field(default=None, ge=0)
    privacy: PrivacyMode = PrivacyMode.FULL
    allow_join: bool = True

    @field_validator("course_name", "course_id")
    @classmethod
    def strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("num_holes")
    @classmethod
    def holes_9_or_18(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value not in (9, 18):
            raise ValueError("num_holes must be 9 or 18.")
        return value

    @field_validator("scores")
    @classmethod
    def validate_scores(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(int(s) < 1 or int(s) > 15 for s in value):
            raise ValueError("Each hole score must be between 1 and 15.")
        return [int(s) for s in value]

    @model_validator(mode="after")
    def playing_needs_course_and_mode(self) -> UpdateStatusRequest:
        if self.state == LiveState.PLAYING:
            if not self.course_name:
                raise ValueError("course_name is required when playing.")
            if self.mode is None:
                raise ValueError("mode is required when playing.")
            if self.hole is None:
                self.hole = 1
        return self


class CreateActivityRequest(BaseModel):
    kind: ActivityKind
    course_name: str | None = Field(default=None, max_length=120)
    mode: PlayMode | None = None
    hole: int | None = Field(default=None, ge=1, le=18)
    total: int | None = Field(default=None, ge=0)
    scores: list[int] | None = None
    privacy: PrivacyMode = PrivacyMode.FULL
    summary: str | None = Field(default=None, max_length=240)
    payload: dict | None = None

    @field_validator("course_name", "summary")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class LiveStatusPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: UserPublic
    state: LiveState
    mode: PlayMode | None
    mode_label: str | None
    course_name: str | None
    course_id: str | None
    hole: int | None
    holes_completed: int | None
    num_holes: int | None
    privacy: PrivacyMode
    show_scores: bool
    scores: list[int]
    total: int | None
    allow_join: bool
    updated_at: datetime


class ActivityPublic(BaseModel):
    id: int
    user: UserPublic
    kind: ActivityKind
    summary: str
    course_name: str | None
    mode: PlayMode | None
    mode_label: str | None
    hole: int | None
    show_scores: bool
    scores: list[int]
    total: int | None
    created_at: datetime
    payload: dict | None = None


class FriendsFeedResponse(BaseModel):
    generated_at: datetime
    live: list[LiveStatusPublic]
    activity: list[ActivityPublic]
