"""FCM fan-out for chat, friend requests, challenges, and side-game actions.

Chat POST and the other event routes must succeed even if this module raises
or FCM is not configured. Callers wrap notify_* in try/except.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services.devices import delete_token_value, tokens_for_users

FCM_LEGACY_URL = "https://fcm.googleapis.com/fcm/send"
FCM_V1_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
_OAUTH_TOKEN: tuple[str, float] | None = None


def fcm_configured() -> bool:
    settings = get_settings()
    return bool(
        (settings.FCM_SERVER_KEY or "").strip()
        or (settings.FCM_SERVICE_ACCOUNT_JSON or "").strip()
    )


def _clip(text: str, limit: int = 80) -> str:
    raw = " ".join((text or "").split())
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 1)].rstrip() + "…"


def _service_account() -> dict[str, Any] | None:
    raw = (get_settings().FCM_SERVICE_ACCOUNT_JSON or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _google_access_token(info: dict[str, Any]) -> str | None:
    global _OAUTH_TOKEN
    now = time.time()
    if _OAUTH_TOKEN and _OAUTH_TOKEN[1] > now + 60:
        return _OAUTH_TOKEN[0]
    try:
        import jwt
    except Exception:
        return None
    email = str(info.get("client_email") or "")
    key = str(info.get("private_key") or "")
    token_uri = str(info.get("token_uri") or "https://oauth2.googleapis.com/token")
    if not email or not key:
        return None
    issued = int(now)
    assertion = jwt.encode(
        {
            "iss": email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": token_uri,
            "iat": issued,
            "exp": issued + 3600,
        },
        key,
        algorithm="RS256",
    )
    body = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        token_uri,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    access = str(payload.get("access_token") or "")
    if not access:
        return None
    _OAUTH_TOKEN = (access, now + float(payload.get("expires_in") or 3500))
    return access


def deliver_fcm(
    *,
    token: str,
    title: str,
    body: str,
    data: dict[str, str],
) -> str:
    """Send one message. Returns ok / unregistered / skipped / error."""
    if not token.strip():
        return "skipped"
    if not fcm_configured():
        return "skipped"
    settings = get_settings()
    account = _service_account()
    project = (settings.FCM_PROJECT_ID or "").strip()
    if account and not project:
        project = str(account.get("project_id") or "").strip()
    if account and project:
        result = _send_v1(account, project, token, title, body, data)
        if result != "error":
            return result
    key = (settings.FCM_SERVER_KEY or "").strip()
    if key:
        return _send_legacy(key, token, title, body, data)
    return "skipped"


def _data_strings(data: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in data.items():
        if value is None:
            continue
        out[str(key)] = str(value)
    return out


def _send_legacy(
    server_key: str,
    token: str,
    title: str,
    body: str,
    data: dict[str, str],
) -> str:
    payload = {
        "to": token,
        "priority": "high",
        "notification": {
            "title": title,
            "body": body,
            "sound": "default",
        },
        "data": _data_strings(data),
    }
    req = urllib.request.Request(
        FCM_LEGACY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"key={server_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if exc.code in {400, 404} or "NotRegistered" in text or "INVALID_ARGUMENT" in text:
            return "unregistered"
        return "error"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "error"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return "ok"
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if isinstance(results, list) and results:
        err = str((results[0] or {}).get("error") or "")
        if err in {"NotRegistered", "InvalidRegistration"}:
            return "unregistered"
        if err:
            return "error"
    return "ok"


def _send_v1(
    account: dict[str, Any],
    project: str,
    token: str,
    title: str,
    body: str,
    data: dict[str, str],
) -> str:
    access = _google_access_token(account)
    if not access:
        return "error"
    payload = {
        "message": {
            "token": token,
            "notification": {"title": title, "body": body},
            "data": _data_strings(data),
            "android": {
                "priority": "HIGH",
                "notification": {
                    "channel_id": "skinscaddy",
                    "sound": "default",
                },
            },
        }
    }
    req = urllib.request.Request(
        FCM_V1_URL.format(project=project),
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            resp.read()
        return "ok"
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        if exc.code in {404, 400} or "UNREGISTERED" in text or "NOT_FOUND" in text:
            return "unregistered"
        return "error"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "error"


def notify_users(
    db: Session,
    user_ids: list[int],
    *,
    title: str,
    body: str,
    kind: str,
    screen: str,
    thread_id: int | None = None,
    challenge_id: int | None = None,
) -> None:
    if not user_ids or not fcm_configured():
        return
    data = {
        "kind": kind,
        "screen": screen,
        "thread_id": str(thread_id or ""),
        "challenge_id": str(challenge_id or ""),
        "title": title,
        "body": body,
    }
    for row in tokens_for_users(db, user_ids):
        result = deliver_fcm(token=row.token, title=title, body=body, data=data)
        if result == "unregistered":
            try:
                delete_token_value(db, row.token)
            except Exception:
                pass


def notify_chat(
    db: Session,
    *,
    actor_id: int,
    actor_name: str,
    member_ids: list[int],
    thread_id: int,
    text: str,
    kind: str = "text",
) -> None:
    others = [i for i in member_ids if i != actor_id]
    if kind == "round":
        line = "Shared a round"
    else:
        line = _clip(text) or "New message"
    notify_users(
        db,
        others,
        title=f"Chat · @{actor_name}",
        body=line,
        kind="chat",
        screen="group_chat",
        thread_id=thread_id,
    )


def notify_friend_request(db: Session, *, addressee_id: int, from_name: str) -> None:
    notify_users(
        db,
        [addressee_id],
        title="Friend request",
        body=f"@{from_name} wants to add you",
        kind="friend",
        screen="activity",
    )


def notify_challenge_event(
    db: Session,
    *,
    recipient_ids: list[int],
    title: str,
    body: str,
    challenge_id: int,
) -> None:
    notify_users(
        db,
        recipient_ids,
        title=title,
        body=body,
        kind="challenge",
        screen="wager",
        challenge_id=challenge_id,
    )
