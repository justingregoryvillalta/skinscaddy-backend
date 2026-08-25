from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.security import create_access_token
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResendVerificationRequest,
    TokenResponse,
    VerifyRequest,
)
from app.schemas.user import UserPublic
from app.services.email import send_verification_email, verification_link
from app.services.signup_profile import SignupProfileError, validate_answers
from app.services.users import (
    AccountDisabledError,
    AccountUnverifiedError,
    EmailTakenError,
    InvalidCredentialsError,
    UsernameTakenError,
    VerificationError,
    authenticate_user,
    create_user,
    resend_verification,
    save_signup_profile,
    verify_user_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserPublic.model_validate(user),
    )


def _expose_verify_secrets() -> bool:
    env = (get_settings().ENV or "").strip().lower()
    return env in {"test", "development", "dev", "local"}


def _register_response(
    user: User,
    raw_token: str,
    email_sent: bool,
    *,
    starting_tokens: int | None = None,
    email_error: str | None = None,
) -> RegisterResponse:
    url = verification_link(raw_token)
    expose = _expose_verify_secrets()
    if email_sent:
        message = (
            "Check your email for an activation link. Your account stays inactive until you tap it."
        )
    else:
        message = email_error or (
            "Account created. Tap Send verification on the next screen — "
            "the activation email was not sent yet."
        )
    return RegisterResponse(
        ok=True,
        username=user.username,
        email=str(user.email or ""),
        message=message,
        email_sent=email_sent,
        email_error=email_error,
        verification_url=url if expose else None,
        verification_token=raw_token if expose else None,
        starting_tokens=starting_tokens,
    )


def _verify_html(title: str, body: str, *, ok: bool) -> HTMLResponse:
    color = "#2f6b3a" if ok else "#8a2b2b"
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin:0; font-family: system-ui, sans-serif; background:#f4f1e8; color:#142016; }}
main {{ max-width:28rem; margin:3rem auto; padding:1.5rem; background:#fff; border-radius:12px; }}
h1 {{ color:{color}; font-size:1.35rem; }}
</style></head>
<body><main><h1>{title}</h1><p>{body}</p>
<p>You can close this page and open SkinsCaddy to log in.</p>
</main></body></html>"""
    return HTMLResponse(html, status_code=200 if ok else 400)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(
    body: RegisterRequest,
    db: Annotated[Session, Depends(get_db)],
) -> RegisterResponse:
    profile = None
    if body.play_intent:
        try:
            profile = validate_answers(
                play_intent=body.play_intent,
                play_style=body.play_style or "",
                skins_frequency=body.skins_frequency or "",
                skins_feel=body.skins_feel,
                skins_pot_band=body.skins_pot_band,
            )
        except SignupProfileError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
    try:
        user, raw_token = create_user(
            db,
            body.username,
            body.password,
            first_name=body.first_name,
            last_name=body.last_name,
            email=body.email,
            postal_code=body.postal_code,
        )
    except UsernameTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except EmailTakenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    starting = None
    if profile is not None:
        saved, _credited = save_signup_profile(
            db,
            user,
            play_intent=str(profile["play_intent"]),
            play_style=str(profile["play_style"]),
            skins_frequency=str(profile["skins_frequency"]),
            skins_feel=profile.get("skins_feel"),
            skins_pot_band=profile.get("skins_pot_band"),
        )
        starting = int(saved["starting_tokens"])
    sent, mail_error = send_verification_email(
        to_email=str(user.email),
        username=user.username,
        raw_token=raw_token,
    )
    return _register_response(
        user,
        raw_token,
        sent,
        starting_tokens=starting,
        email_error=mail_error or None,
    )


@router.get("/verify", response_class=HTMLResponse)
def verify_email_get(
    db: Annotated[Session, Depends(get_db)],
    token: str = Query(min_length=8, max_length=400),
) -> HTMLResponse:
    try:
        user = verify_user_token(db, token)
    except VerificationError as exc:
        return _verify_html("Activation failed", str(exc), ok=False)
    return _verify_html(
        "Account activated",
        f"Thanks, @{user.username}. Your SkinsCaddy account is active. Open the app and log in.",
        ok=True,
    )


@router.post("/verify")
def verify_email_post(
    body: VerifyRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    try:
        user = verify_user_token(db, body.token)
    except VerificationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "ok": True,
        "username": user.username,
        "message": "Account activated. You can log in.",
    }


@router.post("/resend-verification")
@router.post("/send-verification")
def resend_verification_email(
    body: ResendVerificationRequest,
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not (body.email or body.username):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Email or username is required.",
        )
    try:
        user, raw_token = resend_verification(db, email=body.email, username=body.username)
    except InvalidCredentialsError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except VerificationError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    sent, mail_error = send_verification_email(
        to_email=str(user.email),
        username=user.username,
        raw_token=raw_token,
    )
    expose = _expose_verify_secrets()
    if sent:
        message = f"Verification email sent to {user.email}."
    else:
        message = mail_error or "The verification email was not sent."
    return {
        "ok": True,
        "email_sent": sent,
        "email": str(user.email or ""),
        "username": user.username,
        "error": None if sent else (mail_error or message),
        "message": message,
        "verification_url": verification_link(raw_token) if expose else None,
        "verification_token": raw_token if expose else None,
    }


@router.post("/login", response_model=TokenResponse)
def login(
    body: LoginRequest,
    db: Annotated[Session, Depends(get_db)],
) -> TokenResponse:
    try:
        user = authenticate_user(db, body.username, body.password)
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except AccountDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    except AccountUnverifiedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc
    return _token_response(user)
