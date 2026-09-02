from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterDeviceRequest(BaseModel):
    token: str = Field(min_length=8, max_length=4096)
    platform: str = Field(default="android", max_length=16)


class DeviceTokenResponse(BaseModel):
    ok: bool = True
    token: str
    platform: str = "android"
