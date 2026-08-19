from __future__ import annotations

from fastapi.testclient import TestClient


def register(client: TestClient, username: str = "justin", password: str = "password123"):
    return client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["service"] == "skinscaddy"


def test_register_returns_token_and_user(client: TestClient) -> None:
    response = register(client)
    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] == 3600
    assert body["user"]["username"] == "justin"
    assert body["user"]["id"] == 1
    assert "hashed_password" not in body["user"]


def test_register_duplicate_username_is_conflict(client: TestClient) -> None:
    assert register(client).status_code == 201
    response = register(client, username="JUSTIN")
    assert response.status_code == 409
    assert "already taken" in response.json()["detail"].lower()


def test_register_rejects_short_password(client: TestClient) -> None:
    response = register(client, password="short")
    assert response.status_code == 422


def test_register_rejects_invalid_username(client: TestClient) -> None:
    response = register(client, username="bad name!")
    assert response.status_code == 422


def test_login_success(client: TestClient) -> None:
    register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "Justin", "password": "password123"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["user"]["username"] == "justin"


def test_login_wrong_password(client: TestClient) -> None:
    register(client)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": "justin", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert "invalid" in response.json()["detail"].lower()


def test_health_reports_welcome_and_admin(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["welcome_bonus"] == 100
    assert body["admin"] is True


def test_protected_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/protected")
    assert response.status_code == 401


def test_protected_and_me_accept_valid_token(client: TestClient) -> None:
    token = register(client).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    protected = client.get("/api/v1/protected", headers=headers)
    assert protected.status_code == 200
    body = protected.json()
    assert body["ok"] is True
    assert body["user"]["username"] == "justin"

    me = client.get("/api/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "justin"


def test_protected_rejects_garbage_token(client: TestClient) -> None:
    response = client.get(
        "/api/v1/protected",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401
