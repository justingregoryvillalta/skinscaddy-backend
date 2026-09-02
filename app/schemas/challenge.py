from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.challenge import ChallengePlayerRole, ChallengePlayerStatus, ChallengeStatus
from app.schemas.round import RoundPublic
from app.schemas.user import UserPublic


class CreateChallengeRequest(BaseModel):
    usernames: list[str] = Field(min_length=1, max_length=3)
    round_id: int
    wager_amount: int = Field(ge=0, le=1_000_000)
    weeks: int = Field(ge=1, le=4)

    @field_validator("usernames")
    @classmethod
    def normalize_usernames(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            name = raw.strip()
            if not name:
                continue
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(name)
        if not cleaned:
            raise ValueError("Pick 1 to 3 friends to challenge.")
        if len(cleaned) > 3:
            raise ValueError("You can challenge at most 3 friends.")
        return cleaned


class JoinRoundRequest(BaseModel):
    round_id: int
    wager_amount: int = Field(ge=0, le=1_000_000)
    weeks: int = Field(ge=1, le=4)


class SubmitChallengeScoresRequest(BaseModel):
    strokes: int | None = Field(default=None, ge=1, le=15)
    scores: list[int] | None = None

    @field_validator("scores")
    @classmethod
    def validate_scores(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(int(s) < 1 or int(s) > 15 for s in value):
            raise ValueError("Each hole score must be between 1 and 15.")
        return [int(s) for s in value]


class ChallengePlayerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user: UserPublic
    role: ChallengePlayerRole
    status: ChallengePlayerStatus
    scores: list[int]
    total: int | None
    escrowed: bool
    escrow_amount: int
    accepted_at: datetime | None
    finished_at: datetime | None


class ChallengePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: ChallengeStatus
    wager_amount: int
    duration_weeks: int
    deadline: datetime
    pot_amount: int
    course_name: str
    num_holes: int
    source_round: RoundPublic
    creator: UserPublic
    players: list[ChallengePlayerPublic]
    result: dict | None
    created_at: datetime
    updated_at: datetime
    settled_at: datetime | None


class ChallengeListResponse(BaseModel):
    challenges: list[ChallengePublic]
