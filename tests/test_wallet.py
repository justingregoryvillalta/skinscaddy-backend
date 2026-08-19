from __future__ import annotations

from fastapi.testclient import TestClient


def register(client: TestClient, username: str = "justin", password: str = "password123") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def credit(
    client: TestClient,
    token: str,
    amount: int,
    source: str = "reward",
    reason: str | None = "Practice reward",
    reference: str | None = None,
):
    body: dict = {"amount": amount, "source": source}
    if reason is not None:
        body["reason"] = reason
    if reference is not None:
        body["reference"] = reference
    return client.post("/api/v1/wallet/credit", headers=auth(token), json=body)


def debit(
    client: TestClient,
    token: str,
    amount: int,
    source: str = "wager",
    reason: str | None = "Skins wager",
):
    body: dict = {"amount": amount, "source": source}
    if reason is not None:
        body["reason"] = reason
    return client.post("/api/v1/wallet/debit", headers=auth(token), json=body)


def test_wallet_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/wallet").status_code == 401
    assert client.get("/api/v1/wallet/history").status_code == 401
    assert client.post(
        "/api/v1/wallet/credit",
        json={"amount": 10, "source": "reward"},
    ).status_code == 401
    assert client.post(
        "/api/v1/wallet/debit",
        json={"amount": 10, "source": "wager"},
    ).status_code == 401


def test_new_account_starts_with_welcome_bonus(client: TestClient) -> None:
    user = register(client)
    assert user["user"]["token_balance"] == 100
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.status_code == 200
    assert wallet.json() == {"balance": 100, "earned": 100, "spent": 0}

    history = client.get("/api/v1/wallet/history", headers=auth(user["access_token"]))
    assert history.status_code == 200
    assert history.json()["total"] == 1
    entry = history.json()["history"][0]
    assert entry["source"] == "welcome"
    assert entry["amount"] == 100
    assert entry["direction"] == "credit"
    assert entry["reason"] == "Welcome bonus"


def test_login_grants_welcome_if_missing(client: TestClient, db_session) -> None:
    from app.models.user import User
    from app.models.wallet import TokenLedger

    user = register(client, "latebonus")
    uid = user["user"]["id"]
    row = db_session.get(User, uid)
    assert row is not None
    row.token_balance = 0
    db_session.query(TokenLedger).filter(TokenLedger.user_id == uid).delete()
    db_session.commit()

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "latebonus", "password": "password123"},
    )
    assert login.status_code == 200
    wallet = client.get("/api/v1/wallet", headers=auth(login.json()["access_token"]))
    assert wallet.json()["balance"] == 100


def test_credit_increases_balance_and_writes_ledger(client: TestClient) -> None:
    user = register(client)
    response = credit(client, user["access_token"], 50, source="birdie", reason="Birdie on 7")
    assert response.status_code == 201
    body = response.json()
    assert body["balance"] == 150
    assert body["earned"] == 150
    assert body["spent"] == 0
    assert body["entry"]["direction"] == "credit"
    assert body["entry"]["amount"] == 50
    assert body["entry"]["source"] == "birdie"
    assert body["entry"]["reason"] == "Birdie on 7"
    assert body["entry"]["balance_after"] == 150

    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 150


def test_debit_decreases_balance(client: TestClient) -> None:
    user = register(client)
    credit(client, user["access_token"], 40)
    response = debit(client, user["access_token"], 15, source="wager", reason="Hole 3 skin")
    assert response.status_code == 200
    body = response.json()
    assert body["balance"] == 125
    assert body["earned"] == 140
    assert body["spent"] == 15
    assert body["entry"]["direction"] == "debit"
    assert body["entry"]["amount"] == 15
    assert body["entry"]["balance_after"] == 125


def test_debit_cannot_go_below_zero(client: TestClient) -> None:
    user = register(client)
    response = debit(client, user["access_token"], 101)
    assert response.status_code == 409
    assert "insufficient" in response.json()["detail"].lower()

    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json() == {"balance": 100, "earned": 100, "spent": 0}


def test_zero_or_negative_amount_rejected(client: TestClient) -> None:
    user = register(client)
    assert credit(client, user["access_token"], 0).status_code == 422
    assert credit(client, user["access_token"], -5).status_code == 422
    assert debit(client, user["access_token"], 0).status_code == 422


def test_source_must_match_direction(client: TestClient) -> None:
    user = register(client)
    bad_credit = credit(client, user["access_token"], 10, source="wager")
    assert bad_credit.status_code == 400

    credit(client, user["access_token"], 10, source="reward")
    bad_debit = debit(client, user["access_token"], 5, source="birdie")
    assert bad_debit.status_code == 400


def test_history_is_newest_first_and_isolated(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")

    credit(client, alice["access_token"], 20, source="par", reason="Par on 1")
    credit(client, alice["access_token"], 30, source="round_complete_9", reason="Front nine")
    debit(client, alice["access_token"], 10, source="wager", reason="Skin")
    credit(client, bob["access_token"], 99, source="reward", reason="Bob only")

    history = client.get("/api/v1/wallet/history", headers=auth(alice["access_token"]))
    assert history.status_code == 200
    body = history.json()
    assert body["total"] == 4
    reasons = [row["reason"] for row in body["history"]]
    assert reasons[:3] == ["Skin", "Front nine", "Par on 1"]
    assert reasons[3] == "Welcome bonus"
    assert [row["balance_after"] for row in body["history"]] == [140, 150, 120, 100]

    bob_history = client.get("/api/v1/wallet/history", headers=auth(bob["access_token"]))
    assert bob_history.json()["total"] == 2
    assert bob_history.json()["history"][0]["reason"] == "Bob only"

    bob_wallet = client.get("/api/v1/wallet", headers=auth(bob["access_token"]))
    assert bob_wallet.json()["balance"] == 199


def test_default_reason_and_reference(client: TestClient) -> None:
    user = register(client)
    response = credit(
        client,
        user["access_token"],
        100,
        source="round_complete_18",
        reason=None,
        reference="round-42",
    )
    assert response.status_code == 201
    entry = response.json()["entry"]
    assert entry["reason"] == "Completed 18-hole round"
    assert entry["reference"] == "round-42"
    assert entry["source"] == "round_complete_18"


def test_history_pagination(client: TestClient) -> None:
    user = register(client)
    for i in range(3):
        credit(client, user["access_token"], 1, reason=f"hit {i}")

    page = client.get(
        "/api/v1/wallet/history?limit=1&offset=1",
        headers=auth(user["access_token"]),
    )
    assert page.status_code == 200
    assert page.json()["total"] == 4
    assert len(page.json()["history"]) == 1
    assert page.json()["history"][0]["reason"] == "hit 1"
