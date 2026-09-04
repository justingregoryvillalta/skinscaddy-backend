from __future__ import annotations

from fastapi.testclient import TestClient


def register_payload(
    username: str,
    password: str = "password123",
    **overrides: object,
) -> dict:
    data: dict = {
        "username": username,
        "password": password,
        "first_name": "Test",
        "last_name": "User",
        "email": f"{username.lower()}@example.test",
        "postal_code": "M5V 1A1",
        "accept_tos": True,
        "tos_version": "2026-09-04",
    }
    data.update(overrides)
    return data


def register_pending(
    client: TestClient,
    username: str,
    password: str = "password123",
    **overrides: object,
):
    return client.post(
        "/api/v1/auth/register",
        json=register_payload(username, password, **overrides),
    )


def register(
    client: TestClient,
    username: str = "justin",
    password: str = "password123",
    **overrides: object,
) -> dict:
    """Register, activate via the emailed token, then log in."""
    pending = register_pending(client, username, password, **overrides)
    assert pending.status_code == 201, pending.text
    body = pending.json()
    token = body.get("verification_token")
    assert token, body
    verified = client.post("/api/v1/auth/verify", json={"token": token})
    assert verified.status_code == 200, verified.text
    login = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login.status_code == 200, login.text
    return login.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
