from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user import UserPublic


class CreateRoundRequest(BaseModel):
    course_name: str = Field(min_length=1, max_length=120)
    course_id: str | None = Field(default=None, max_length=64)
    num_holes: int = Field(ge=9, le=18)
    scores: list[int] = Field(min_length=9, max_length=18)
    pars: list[int] | None = None

    @field_validator("course_name")
    @classmethod
    def strip_course(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Course name is required.")
        return name

    @field_validator("course_id")
    @classmethod
    def strip_course_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("num_holes")
    @classmethod
    def holes_must_be_9_or_18(cls, value: int) -> int:
        if value not in (9, 18):
            raise ValueError("num_holes must be 9 or 18.")
        return value

    @field_validator("scores")
    @classmethod
    def validate_scores(cls, value: list[int]) -> list[int]:
        if any(int(s) < 1 or int(s) > 15 for s in value):
            raise ValueError("Each hole score must be between 1 and 15.")
        return [int(s) for s in value]

    @model_validator(mode="after")
    def scores_match_holes(self) -> CreateRoundRequest:
        if len(self.scores) != self.num_holes:
            raise ValueError(f"scores must contain exactly {self.num_holes} holes.")
        if self.pars is not None and len(self.pars) != self.num_holes:
            raise ValueError(f"pars must contain exactly {self.num_holes} holes.")
        return self


class RoundPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    course_name: str
    course_id: str | None
    num_holes: int
    scores: list[int]
    pars: list[int] | None
    total: int
    created_at: datetime
    user: UserPublic


class RoundListResponse(BaseModel):
    rounds: list[RoundPublic]
