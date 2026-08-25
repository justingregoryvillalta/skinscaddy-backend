from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import auth, register, register_pending


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["service"] == "skinscaddy"


def test_register_creates_unverified_account(client: TestClient) -> None:
    response = register_pending(client, "justin")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["username"] == "justin"
    assert body["email"] == "justin@example.test"
    assert "access_token" not in body
    assert body["verification_token"]
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "justin", "password": "password123"},
    )
    assert login.status_code == 403
    assert "verify" in login.json()["detail"].lower()


def test_verify_then_login(client: TestClient) -> None:
    body = register(client)
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["username"] == "justin"
    assert body["user"]["email"] == "justin@example.test"
    assert body["user"]["first_name"] == "Test"
    assert body["user"]["is_verified"] is True
    assert "hashed_password" not in body["user"]


def test_register_duplicate_verified_username_is_conflict(client: TestClient) -> None:
    register(client, "justin")
    response = register_pending(client, "JUSTIN", email="other@example.test")
    assert response.status_code == 409
    assert "already taken" in response.json()["detail"].lower()


def test_register_duplicate_verified_email_is_conflict(client: TestClient) -> None:
    register(client, "alpha", email="same@example.test")
    response = register_pending(client, "bravo", email="same@example.test")
    assert response.status_code == 409
    assert "email" in response.json()["detail"].lower()


def test_register_rejects_short_password(client: TestClient) -> None:
    response = register_pending(client, "justin", password="short")
    assert response.status_code == 422


def test_register_rejects_invalid_username(client: TestClient) -> None:
    response = register_pending(client, "bad name!")
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


def test_register_reserved_admin_username(client: TestClient) -> None:
    response = register_pending(client, "admin")
    assert response.status_code == 409
    assert "already taken" in response.json()["detail"].lower()
    assert register_pending(client, "Admin").status_code == 409


def test_health_reports_welcome_and_admin(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["welcome_bonus"] == 100
    assert body["admin"] is True
    assert "mail_configured" in body


def test_protected_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/protected")
    assert response.status_code == 401


def test_protected_and_me_accept_valid_token(client: TestClient) -> None:
    token = register(client)["access_token"]
    headers = auth(token)

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


def test_verify_get_activates_account(client: TestClient) -> None:
    pending = register_pending(client, "pat")
    token = pending.json()["verification_token"]
    page = client.get("/api/v1/auth/verify", params={"token": token})
    assert page.status_code == 200
    assert "activated" in page.text.lower()
    login = client.post(
        "/api/v1/auth/login",
        json={"username": "pat", "password": "password123"},
    )
    assert login.status_code == 200


def test_send_verification_resends_for_unverified_user(client: TestClient) -> None:
    pending = register_pending(client, "unverifiedsend", email="unverifiedsend@example.test")
    assert pending.status_code == 201
    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "unverifiedsend", "password": "password123"},
    )
    assert blocked.status_code == 403
    sent = client.post(
        "/api/v1/auth/send-verification",
        json={"username": "unverifiedsend", "email": "unverifiedsend@example.test"},
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["email"] == "unverifiedsend@example.test"
    assert "email_sent" in body
    token = body.get("verification_token")
    assert token
    assert client.post("/api/v1/auth/verify", json={"token": token}).status_code == 200
    ok = client.post(
        "/api/v1/auth/login",
        json={"username": "unverifiedsend", "password": "password123"},
    )
    assert ok.status_code == 200


def test_unverified_can_reregister_same_email(client: TestClient) -> None:
    first = register_pending(client, "oldname", email="same.inbox@example.test")
    assert first.status_code == 201
    second = register_pending(client, "newname", email="same.inbox@example.test")
    assert second.status_code == 201, second.text
    sent = client.post(
        "/api/v1/auth/send-verification",
        json={"email": "same.inbox@example.test", "username": "newname"},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["email"] == "same.inbox@example.test"


def test_send_verification_alias_matches_resend(client: TestClient) -> None:
    register_pending(client, "mailalias")
    resent = client.post(
        "/api/v1/auth/send-verification",
        json={"username": "mailalias", "email": "mailalias@example.test"},
    )
    assert resent.status_code == 200, resent.text
    body = resent.json()
    assert "email_sent" in body
    assert body["email"] == "mailalias@example.test"


def test_resend_reports_when_mail_not_configured(client: TestClient) -> None:
    register_pending(client, "mailfail")
    resent = client.post(
        "/api/v1/auth/resend-verification",
        json={"username": "mailfail", "email": "mailfail@example.test"},
    )
    assert resent.status_code == 200, resent.text
    body = resent.json()
    assert body["email_sent"] is False
    assert body["email"] == "mailfail@example.test"
    assert "smtp" in (body.get("error") or "").lower() or "mail" in (body.get("error") or "").lower()
    assert body.get("verification_token")


def test_resend_verification(client: TestClient) -> None:
    register_pending(client, "sam")
    denied = client.post(
        "/api/v1/auth/login",
        json={"username": "sam", "password": "password123"},
    )
    assert denied.status_code == 403
    resent = client.post(
        "/api/v1/auth/resend-verification",
        json={"username": "sam"},
    )
    assert resent.status_code == 200
    token = resent.json()["verification_token"]
    assert client.post("/api/v1/auth/verify", json={"token": token}).status_code == 200
    ok = client.post(
        "/api/v1/auth/login",
        json={"username": "sam", "password": "password123"},
    )
    assert ok.status_code == 200
