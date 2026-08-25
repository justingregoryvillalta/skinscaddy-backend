from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.signup_profile import starting_tokens
from tests.helpers import auth, register, register_pending


def test_starting_token_grid() -> None:
    assert starting_tokens("score", "solo", "never") == 150
    assert starting_tokens("practice", "solo", "sometimes") == 200
    assert starting_tokens("skins", "group", "never") == 150
    assert starting_tokens("skins", "mix", "sometimes", "small", "low") == 250
    assert starting_tokens("skins", "mix", "sometimes", "small", "medium") == 400
    assert starting_tokens("skins", "mix", "sometimes", "weekend", "low") == 400
    assert starting_tokens("skins", "mix", "sometimes", "weekend", "medium") == 600
    assert starting_tokens("skins", "mix", "sometimes", "weekend", "high") == 900
    assert starting_tokens("skins", "mix", "most", "serious", "medium") == 1000
    assert starting_tokens("skins", "mix", "most", "serious", "high") == 1500
    assert starting_tokens("skins", "mix", "most", "high_table", "high") == 2500
    assert starting_tokens("skins", "mix", "most", "high_table", "medium") == 1800
    assert starting_tokens("skins", "mix", "sometimes", "weekend", "varies") == 800
    assert starting_tokens("skins", "mix", "sometimes") == 600
    # Just getting into it floors feel at the Small row even if they picked High.
    assert starting_tokens("skins", "mix", "learning", "high_table", "high") == 400


def test_signup_profile_credits_starting_wallet(client: TestClient) -> None:
    user = register(client, "chippy")
    assert user["user"]["token_balance"] == 100
    assert user["user"]["signup_complete"] is False
    headers = auth(user["access_token"])
    saved = client.post(
        "/api/v1/me/signup-profile",
        headers=headers,
        json={
            "play_intent": "skins",
            "play_style": "group",
            "skins_frequency": "most",
            "skins_feel": "high_table",
            "skins_pot_band": "high",
        },
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["starting_tokens"] == 2500
    assert body["token_balance"] == 2500
    assert body["credited"] == 2400
    wallet = client.get("/api/v1/wallet", headers=headers)
    assert wallet.json()["balance"] == 2500
    me = client.get("/api/v1/me", headers=headers)
    assert me.json()["signup_complete"] is True
    assert me.json()["play_intent"] == "skins"
    again = client.post(
        "/api/v1/me/signup-profile",
        headers=headers,
        json={
            "play_intent": "score",
            "play_style": "solo",
            "skins_frequency": "never",
        },
    )
    assert again.status_code == 409


def test_score_only_skips_skins_detail_and_gets_150(client: TestClient) -> None:
    user = register(client, "scorer")
    saved = client.post(
        "/api/v1/me/signup-profile",
        headers=auth(user["access_token"]),
        json={
            "play_intent": "score",
            "play_style": "group",
            "skins_frequency": "sometimes",
            "skins_feel": "high_table",
            "skins_pot_band": "high",
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["starting_tokens"] == 150
    assert saved.json()["skins_feel"] is None
    assert saved.json()["skins_pot_band"] is None
    assert saved.json()["token_balance"] == 150


def test_first_skins_topup_for_practice_account(client: TestClient) -> None:
    user = register(client, "putter")
    headers = auth(user["access_token"])
    saved = client.post(
        "/api/v1/me/signup-profile",
        headers=headers,
        json={
            "play_intent": "practice",
            "play_style": "solo",
            "skins_frequency": "never",
        },
    )
    assert saved.json()["token_balance"] == 200
    top = client.post(
        "/api/v1/wallet/first-skins-topup",
        headers=headers,
        json={"pot_per_hole": 20},
    )
    assert top.status_code == 200, top.text
    assert top.json()["target"] == 360
    assert top.json()["applied"] is True
    assert top.json()["token_balance"] == 360
    again = client.post(
        "/api/v1/wallet/first-skins-topup",
        headers=headers,
        json={"pot_per_hole": 50},
    )
    assert again.json()["applied"] is False
    assert again.json()["token_balance"] == 360


def test_register_with_quiz_credits_chips_without_login(client: TestClient) -> None:
    pending = register_pending(
        client,
        "chipnew",
        play_intent="skins",
        play_style="group",
        skins_frequency="most",
        skins_feel="high_table",
        skins_pot_band="high",
    )
    assert pending.status_code == 201, pending.text
    body = pending.json()
    assert "access_token" not in body
    assert body["starting_tokens"] == 2500
    blocked = client.post(
        "/api/v1/auth/login",
        json={"username": "chipnew", "password": "password123"},
    )
    assert blocked.status_code == 403
    token = body.get("verification_token")
    assert token
    assert client.post("/api/v1/auth/verify", json={"token": token}).status_code == 200
    logged = client.post(
        "/api/v1/auth/login",
        json={"username": "chipnew", "password": "password123"},
    )
    assert logged.status_code == 200, logged.text
    user = logged.json()["user"]
    assert user["signup_complete"] is True
    assert user["token_balance"] == 2500
    assert user["is_verified"] is True


def test_skins_path_does_not_auto_topup(client: TestClient) -> None:
    user = register(client, "skinner")
    headers = auth(user["access_token"])
    client.post(
        "/api/v1/me/signup-profile",
        headers=headers,
        json={
            "play_intent": "skins",
            "play_style": "mix",
            "skins_frequency": "sometimes",
            "skins_feel": "weekend",
            "skins_pot_band": "medium",
        },
    )
    top = client.post(
        "/api/v1/wallet/first-skins-topup",
        headers=headers,
        json={"pot_per_hole": 50},
    )
    assert top.json()["applied"] is False
    assert top.json()["token_balance"] == 600
