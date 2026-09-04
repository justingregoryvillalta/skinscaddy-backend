from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.user import (
    AcceptTosRequest,
    FirstSkinsTopupRequest,
    FirstSkinsTopupResponse,
    SignupProfileRequest,
    SignupProfileResponse,
    UserPublic,
)
from app.services.signup_profile import SignupProfileError, parse_profile
from app.services.users import (
    accept_tos,
    apply_first_skins_topup,
    save_signup_profile,
)

router = APIRouter(tags=["users"])


@router.get("/me", response_model=UserPublic)
def read_me(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    return current_user


@router.post("/me/tos", response_model=UserPublic)
def post_tos(
    body: AcceptTosRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    return accept_tos(db, current_user, body.tos_version)


@router.get("/protected")
def protected_example(current_user: Annotated[User, Depends(get_current_user)]) -> dict:
    return {
        "ok": True,
        "message": "Token is valid.",
        "user": UserPublic.model_validate(current_user).model_dump(mode="json"),
    }


@router.post("/me/signup-profile", response_model=SignupProfileResponse)
def post_signup_profile(
    body: SignupProfileRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> SignupProfileResponse:
    try:
        profile, credited = save_signup_profile(
            db,
            current_user,
            play_intent=body.play_intent,
            play_style=body.play_style,
            skins_frequency=body.skins_frequency,
            skins_feel=body.skins_feel,
            skins_pot_band=body.skins_pot_band,
        )
    except SignupProfileError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return SignupProfileResponse(
        play_intent=str(profile["play_intent"]),
        play_style=str(profile["play_style"]),
        skins_frequency=str(profile["skins_frequency"]),
        skins_feel=profile.get("skins_feel"),
        skins_pot_band=profile.get("skins_pot_band"),
        starting_tokens=int(profile["starting_tokens"]),
        token_balance=int(current_user.token_balance or 0),
        credited=int(credited),
        skins_topup_done=bool(profile.get("skins_topup_done")),
        message=(
            "You'll pick the pot each skins round. Chips stay in the app."
        ),
    )


@router.get("/me/signup-profile", response_model=SignupProfileResponse)
def get_signup_profile(
    current_user: Annotated[User, Depends(get_current_user)],
) -> SignupProfileResponse:
    profile = parse_profile(getattr(current_user, "signup_profile", None))
    if not profile or profile.get("starting_tokens") is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Signup questions not completed yet.",
        )
    return SignupProfileResponse(
        play_intent=str(profile.get("play_intent") or ""),
        play_style=str(profile.get("play_style") or ""),
        skins_frequency=str(profile.get("skins_frequency") or ""),
        skins_feel=profile.get("skins_feel"),
        skins_pot_band=profile.get("skins_pot_band"),
        starting_tokens=int(profile.get("starting_tokens") or 0),
        token_balance=int(current_user.token_balance or 0),
        credited=0,
        skins_topup_done=bool(profile.get("skins_topup_done")),
        message="You'll pick the pot each skins round. Chips stay in the app.",
    )


@router.post("/wallet/first-skins-topup", response_model=FirstSkinsTopupResponse)
def post_first_skins_topup(
    body: FirstSkinsTopupRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> FirstSkinsTopupResponse:
    result = apply_first_skins_topup(db, current_user, int(body.pot_per_hole))
    return FirstSkinsTopupResponse(
        token_balance=int(result["token_balance"]),
        credited=int(result["credited"]),
        target=int(result["target"]),
        applied=bool(result["applied"]),
        message=str(result.get("message") or ""),
    )
