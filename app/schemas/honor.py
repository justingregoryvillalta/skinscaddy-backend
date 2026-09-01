from __future__ import annotations

from pydantic import BaseModel, Field


class HonorSeason(BaseModel):
    id: str
    name: str
    window: str


class HonorTagRow(BaseModel):
    id: str
    name: str
    mark: str
    what: str = ""
    how: str = ""
    earned: bool = False
    locked: bool = False
    earned_at: str = ""
    icon: str = ""
    metal: str = ""
    count: int = 0
    next_at: int | None = None


class HonorStats(BaseModel):
    earned: int = 0
    spent: int = 0
    skins_taken: int = 0
    birdies: int = 0
    tally: int = 0
    round_count: int = 0
    friend_count: int = 0
    challenge_count: int = 0
    ledger: int = 0


class HonorSnapshot(BaseModel):
    season: HonorSeason
    stats: HonorStats
    tags: list[HonorTagRow]
    earned_count: int = 0
    tag_total: int = 0
    tally: int = 0
    rank_friends: int | None = None


class HonorSyncRequest(BaseModel):
    skins_taken: int | None = Field(default=None, ge=0, le=1_000_000)
    birdies: int | None = Field(default=None, ge=0, le=1_000_000)
    round_count: int | None = Field(default=None, ge=0, le=1_000_000)
    friend_count: int | None = Field(default=None, ge=0, le=1_000_000)
    challenge_count: int | None = Field(default=None, ge=0, le=1_000_000)


class HonorTagCompact(BaseModel):
    id: str
    metal: str = ""
    count: int = 0


class HonorFriendsEntry(BaseModel):
    rank: int
    username: str
    tally: int
    earned_count: int
    regular_metal: str = ""
    tags: list[HonorTagCompact] = Field(default_factory=list)


class HonorFriendsResponse(BaseModel):
    season: HonorSeason
    entries: list[HonorFriendsEntry]


class HonorHotEntry(BaseModel):
    rank: int
    username: str
    tally: int
    earned_count: int
    regular_metal: str = ""


class HonorHotResponse(BaseModel):
    season: HonorSeason
    entries: list[HonorHotEntry]
