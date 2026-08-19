from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.scramble import ScrambleStatus
from app.schemas.user import UserPublic


class CreateTeamInput(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    start_hole: int | None = Field(default=None, ge=1, le=18)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Team name is required.")
        return name


class CreateScrambleRequest(BaseModel):
    course_name: str = Field(min_length=1, max_length=120)
    course_id: str | None = Field(default=None, max_length=64)
    num_holes: int = 9
    wager_amount: int = Field(ge=1, le=1_000_000)
    teams: list[CreateTeamInput] = Field(min_length=2, max_length=6)
    host_team_index: int = Field(ge=0)
    pars: list[int] | None = None

    @field_validator("course_name")
    @classmethod
    def strip_course(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Course name is required.")
        return name

    @field_validator("num_holes")
    @classmethod
    def holes_9_or_18(cls, value: int) -> int:
        if value not in (9, 18):
            raise ValueError("num_holes must be 9 or 18.")
        return value

    @model_validator(mode="after")
    def validate_teams_and_pars(self) -> CreateScrambleRequest:
        if self.host_team_index >= len(self.teams):
            raise ValueError("host_team_index is out of range.")
        if self.pars is not None and len(self.pars) != self.num_holes:
            raise ValueError(f"pars must contain exactly {self.num_holes} holes.")
        return self


class JoinScrambleRequest(BaseModel):
    code: str = Field(min_length=4, max_length=16)
    team_index: int = Field(ge=0)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return "".join(ch for ch in value.upper() if ch.isalnum())


class PostScrambleScoreRequest(BaseModel):
    strokes: int = Field(ge=1, le=15)
    hole: int | None = Field(default=None, ge=1, le=18)


class ScrambleMemberPublic(BaseModel):
    user: UserPublic
    team_index: int


class ScrambleHolePublic(BaseModel):
    hole: int
    revealed: bool
    settled: bool
    posted: list[bool]
    scores: list[int | None]


class ScrambleTeamPublic(BaseModel):
    index: int
    name: str
    start_hole: int
    current_hole: int
    holes_played: int
    skins_won: int
    finished: bool
    members: list[UserPublic]
    scores: list[int | None]


class ScrambleStatePublic(BaseModel):
    id: int
    join_code: str
    deep_link: str
    status: ScrambleStatus
    course_name: str
    course_id: str | None
    num_holes: int
    wager_amount: int
    skin_unit: int
    skin_pot: int
    skin_stack: int
    revision: int
    host: UserPublic
    my_team_index: int | None
    teams: list[ScrambleTeamPublic]
    holes: list[ScrambleHolePublic]
    skin_results: list[dict]
    created_at: datetime
    updated_at: datetime


class ScramblePreviewPublic(BaseModel):
    join_code: str
    deep_link: str
    status: ScrambleStatus
    course_name: str
    num_holes: int
    wager_amount: int
    teams: list[dict]


class ScrambleListResponse(BaseModel):
    scrambles: list[ScrambleStatePublic]
