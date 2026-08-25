"""Transactional email via SMTP (Render-friendly env vars).

Gmail: SMTP_PASSWORD must be a Gmail App Password. A normal account
password is rejected with SMTP 535. Do not disable SMTP login to work around that.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings


def smtp_configured() -> bool:
    settings = get_settings()
    host = (settings.SMTP_HOST or "").strip()
    from_addr = (settings.SMTP_FROM or settings.SMTP_USERNAME or "").strip()
    return bool(host and from_addr)


def smtp_missing_reason() -> str:
    settings = get_settings()
    if not (settings.SMTP_HOST or "").strip():
        return (
            "Mail is not configured on the server (set SMTP_HOST, SMTP_FROM, "
            "SMTP_USERNAME, and SMTP_PASSWORD on Render)."
        )
    if not (settings.SMTP_FROM or settings.SMTP_USERNAME or "").strip():
        return "Mail is not configured on the server (set SMTP_FROM or SMTP_USERNAME)."
    return ""


def public_base_url() -> str:
    settings = get_settings()
    for candidate in (settings.APP_BASE_URL, settings.PUBLIC_BASE_URL):
        url = (candidate or "").strip().rstrip("/")
        if url:
            return url
    return "http://127.0.0.1:8000"


def verification_link(raw_token: str) -> str:
    from urllib.parse import quote

    token = (raw_token or "").strip()
    return f"{public_base_url()}/api/v1/auth/verify?token={quote(token, safe='')}"


def send_verification_email(*, to_email: str, username: str, raw_token: str) -> tuple[bool, str]:
    """Send the activation email. Returns (sent, error). Never marks the account verified."""
    settings = get_settings()
    dest = (to_email or "").strip()
    if "@" not in dest or "." not in dest:
        return False, "That email address is not valid."
    if not smtp_configured():
        reason = smtp_missing_reason()
        print(
            f"SMTP not configured — verification URL for @{username}: "
            f"{verification_link(raw_token)}",
            flush=True,
        )
        return False, reason

    link = verification_link(raw_token)
    from_addr = (settings.SMTP_FROM or settings.SMTP_USERNAME or "").strip()
    msg = EmailMessage()
    msg["Subject"] = "Activate your SkinsCaddy account"
    msg["From"] = from_addr
    msg["To"] = dest
    msg.set_content(
        f"Hi {username},\n\n"
        "Thanks for joining SkinsCaddy. Tap the link below to activate your account. "
        "The account stays inactive until you do this — it helps us keep kids from "
        "signing up without a real email.\n\n"
        f"{link}\n\n"
        "After you activate, open SkinsCaddy and log in with your username and password.\n\n"
        f"This link expires in {int(settings.VERIFICATION_HOURS)} hours.\n\n"
        "If you did not create this account, you can ignore this email.\n\n"
        "— SkinsCaddy\n"
    )
    msg.add_alternative(
        "<p>Hi "
        f"{_escape(username)}"
        ",</p>"
        "<p>Thanks for joining SkinsCaddy. Tap the button below to activate your account. "
        "The account stays inactive until you do this — it helps us keep kids from "
        "signing up without a real email.</p>"
        f'<p><a href="{_escape(link)}">Activate my account</a></p>'
        "<p>After you activate, open SkinsCaddy and log in with your username and password.</p>"
        f"<p>This link expires in {int(settings.VERIFICATION_HOURS)} hours.</p>"
        "<p>If you did not create this account, you can ignore this email.</p>"
        "<p>— SkinsCaddy</p>",
        subtype="html",
    )

    host = settings.SMTP_HOST.strip()
    port = int(settings.SMTP_PORT or 587)
    user = (settings.SMTP_USERNAME or "").strip()
    password = settings.SMTP_PASSWORD or ""
    try:
        if settings.SMTP_USE_SSL or port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.ehlo()
                if settings.SMTP_USE_TLS:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if user:
                    smtp.login(user, password)
                smtp.send_message(msg)
        print(f"verification email sent to {dest} (@{username}) from {from_addr}", flush=True)
        return True, ""
    except Exception as exc:
        detail = str(exc)
        if "535" in detail:
            err = (
                "SMTP send failed: Gmail rejected the login (535). "
                "SMTP_PASSWORD must be a Gmail App Password, not the Google account password."
            )
        else:
            err = f"SMTP send failed: {exc}"
        print(f"verification email failed for @{username} → {dest}: {exc}", flush=True)
        print(f"verification URL: {link}", flush=True)
        return False, err


def _escape(value: str) -> str:
    return (
        (value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
