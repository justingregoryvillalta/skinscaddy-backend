from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.device import (
    DeviceTokenResponse,
    RegisterDeviceRequest,
)
from app.services.devices import register_device_token, unregister_device_token

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/fcm", response_model=DeviceTokenResponse)
def post_fcm_token(
    body: RegisterDeviceRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> DeviceTokenResponse:
    """Register this install's FCM token after login. Same token on a new
    account is reassigned so a shared phone does not keep notifying the
    previous user.
    """
    row = register_device_token(
        db, current_user, token=body.token, platform=body.platform
    )
    return DeviceTokenResponse(ok=True, token=row.token, platform=row.platform)


@router.delete("/fcm", response_model=DeviceTokenResponse)
def delete_fcm_token(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    token: Annotated[str, Query(min_length=8, max_length=4096)],
) -> DeviceTokenResponse:
    unregister_device_token(db, current_user, token)
    return DeviceTokenResponse(ok=True, token=token, platform="android")
