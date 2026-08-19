from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.challenge import Challenge


def register(client: TestClient, username: str, password: str = "password123") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def become_friends(client: TestClient, a: dict, b: dict) -> None:
    req = client.post(
        "/api/v1/friends/requests",
        headers=auth(a["access_token"]),
        json={"username": b["user"]["username"]},
    )
    assert req.status_code == 201, req.text
    acc = client.post(
        f"/api/v1/friends/requests/{req.json()['id']}/accept",
        headers=auth(b["access_token"]),
    )
    assert acc.status_code == 200, acc.text


def credit(client: TestClient, token: str, amount: int) -> None:
    response = client.post(
        "/api/v1/wallet/credit",
        headers=auth(token),
        json={"amount": amount, "source": "reward", "reason": "Test bankroll"},
    )
    assert response.status_code == 201, response.text


def debit(client: TestClient, token: str, amount: int) -> None:
    response = client.post(
        "/api/v1/wallet/debit",
        headers=auth(token),
        json={"amount": amount, "source": "wager", "reason": "Drain welcome"},
    )
    assert response.status_code == 200, response.text


def add_round(client: TestClient, token: str, total_per_hole: int = 4) -> dict:
    scores = [total_per_hole] * 9
    response = client.post(
        "/api/v1/rounds",
        headers=auth(token),
        json={"course_name": "Pebble Beach", "num_holes": 9, "scores": scores},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_challenge(
    client: TestClient,
    token: str,
    usernames: list[str],
    round_id: int,
    wager: int = 10,
    weeks: int = 1,
):
    return client.post(
        "/api/v1/challenges",
        headers=auth(token),
        json={
            "usernames": usernames,
            "round_id": round_id,
            "wager_amount": wager,
            "weeks": weeks,
        },
    )


def test_challenge_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/challenges").status_code == 401
    assert client.get("/api/v1/rounds").status_code == 401
    assert client.post(
        "/api/v1/challenges",
        json={"usernames": ["bob"], "round_id": 1, "wager_amount": 5, "weeks": 1},
    ).status_code == 401


def test_cannot_challenge_self_or_non_friend(client: TestClient) -> None:
    alice = register(client, "alice")
    register(client, "bob")
    rnd = add_round(client, alice["access_token"])

    self_hit = create_challenge(client, alice["access_token"], ["alice"], rnd["id"])
    assert self_hit.status_code == 409

    stranger = create_challenge(client, alice["access_token"], ["bob"], rnd["id"])
    assert stranger.status_code == 403
    assert "friends" in stranger.json()["detail"].lower()


def test_cannot_challenge_more_than_three_friends(client: TestClient) -> None:
    alice = register(client, "alice")
    friends = [register(client, name) for name in ("bob", "cara", "dana", "erin")]
    for pal in friends:
        become_friends(client, alice, pal)
    rnd = add_round(client, alice["access_token"])
    response = create_challenge(
        client,
        alice["access_token"],
        [p["user"]["username"] for p in friends],
        rnd["id"],
    )
    assert response.status_code == 422


def test_create_accept_and_complete_pays_winner(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    credit(client, alice["access_token"], 50)
    credit(client, bob["access_token"], 50)
    rnd = add_round(client, alice["access_token"], total_per_hole=4)

    created = create_challenge(client, alice["access_token"], ["bob"], rnd["id"], wager=10)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending"
    assert body["wager_amount"] == 10
    assert body["pot_amount"] == 0
    assert body["duration_weeks"] == 1
    assert len(body["players"]) == 2

    incoming = client.get("/api/v1/challenges/incoming", headers=auth(bob["access_token"]))
    assert incoming.status_code == 200
    assert len(incoming.json()["challenges"]) == 1

    accepted = client.post(
        f"/api/v1/challenges/{body['id']}/accept",
        headers=auth(bob["access_token"]),
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "active"
    assert accepted.json()["pot_amount"] == 20

    assert client.get("/api/v1/wallet", headers=auth(alice["access_token"])).json()["balance"] == 140
    assert client.get("/api/v1/wallet", headers=auth(bob["access_token"])).json()["balance"] == 140

    scores = client.post(
        f"/api/v1/challenges/{body['id']}/scores",
        headers=auth(bob["access_token"]),
        json={"scores": [3] * 9},
    )
    assert scores.status_code == 200, scores.text
    done = scores.json()
    assert done["status"] == "completed"
    assert done["pot_amount"] == 0
    assert done["result"]["kind"] == "completed"
    winner_ids = done["result"]["winner_ids"]
    assert bob["user"]["id"] in winner_ids

    assert client.get("/api/v1/wallet", headers=auth(bob["access_token"])).json()["balance"] == 160
    assert client.get("/api/v1/wallet", headers=auth(alice["access_token"])).json()["balance"] == 140


def test_decline_then_all_declined_expires(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    credit(client, alice["access_token"], 20)
    rnd = add_round(client, alice["access_token"])
    created = create_challenge(client, alice["access_token"], ["bob"], rnd["id"], wager=10)
    cid = created.json()["id"]

    declined = client.post(
        f"/api/v1/challenges/{cid}/decline",
        headers=auth(bob["access_token"]),
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "expired"
    assert client.get("/api/v1/wallet", headers=auth(alice["access_token"])).json()["balance"] == 120


def test_only_invitee_can_accept(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    become_friends(client, alice, bob)
    become_friends(client, alice, cara)
    credit(client, alice["access_token"], 20)
    rnd = add_round(client, alice["access_token"])
    cid = create_challenge(client, alice["access_token"], ["bob"], rnd["id"]).json()["id"]

    assert (
        client.post(
            f"/api/v1/challenges/{cid}/accept",
            headers=auth(alice["access_token"]),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/challenges/{cid}/accept",
            headers=auth(cara["access_token"]),
        ).status_code
        == 403
    )


def test_accept_requires_tokens(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    credit(client, alice["access_token"], 20)
    debit(client, bob["access_token"], 100)
    rnd = add_round(client, alice["access_token"])
    cid = create_challenge(client, alice["access_token"], ["bob"], rnd["id"], wager=10).json()["id"]
    poor = client.post(
        f"/api/v1/challenges/{cid}/accept",
        headers=auth(bob["access_token"]),
    )
    assert poor.status_code == 409
    assert "enough tokens" in poor.json()["detail"].lower()


def test_forfeit_on_deadline_pays_finishers(
    client: TestClient,
    db_session: Session,
) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    credit(client, alice["access_token"], 30)
    credit(client, bob["access_token"], 30)
    rnd = add_round(client, alice["access_token"])
    cid = create_challenge(client, alice["access_token"], ["bob"], rnd["id"], wager=10).json()["id"]
    assert client.post(
        f"/api/v1/challenges/{cid}/accept",
        headers=auth(bob["access_token"]),
    ).status_code == 200

    row = db_session.get(Challenge, cid)
    assert row is not None
    row.deadline = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    settled = client.post(
        f"/api/v1/challenges/{cid}/settle",
        headers=auth(alice["access_token"]),
    )
    assert settled.status_code == 200, settled.text
    body = settled.json()
    assert body["status"] == "forfeited"
    assert body["result"]["kind"] == "forfeited"
    assert alice["user"]["id"] in body["result"]["winner_ids"]
    assert client.get("/api/v1/wallet", headers=auth(alice["access_token"])).json()["balance"] == 140
    assert client.get("/api/v1/wallet", headers=auth(bob["access_token"])).json()["balance"] == 120


def test_history_lists_challenges_for_each_user(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    credit(client, alice["access_token"], 10)
    rnd = add_round(client, alice["access_token"])
    create_challenge(client, alice["access_token"], ["bob"], rnd["id"], wager=0)

    alice_hist = client.get("/api/v1/challenges", headers=auth(alice["access_token"]))
    bob_hist = client.get("/api/v1/challenges", headers=auth(bob["access_token"]))
    assert alice_hist.status_code == 200
    assert len(alice_hist.json()["challenges"]) == 1
    assert len(bob_hist.json()["challenges"]) == 1
    assert client.get("/api/v1/challenges/outgoing", headers=auth(alice["access_token"])).json()[
        "challenges"
    ]
    assert client.get("/api/v1/rounds", headers=auth(alice["access_token"])).json()["rounds"]


def test_unknown_user_and_foreign_round(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    alice_round = add_round(client, alice["access_token"])
    bob_round = add_round(client, bob["access_token"])
    missing = create_challenge(client, alice["access_token"], ["nobody"], alice_round["id"])
    assert missing.status_code == 404
    stolen = create_challenge(client, alice["access_token"], ["bob"], bob_round["id"])
    assert stolen.status_code == 403
