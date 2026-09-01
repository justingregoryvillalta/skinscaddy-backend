from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import auth, register


HOT_ENTRY_KEYS = {"rank", "username", "tally", "earned_count", "regular_metal"}
WALLET_FIELDS = {
    "email",
    "token_balance",
    "balance",
    "earned",
    "spent",
    "wallet",
    "scores",
    "hole_cards",
    "first_name",
    "last_name",
}


def _befriend(client: TestClient, alice: dict, bob: dict) -> None:
    sent = client.post(
        "/api/v1/friends/requests",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    assert sent.status_code == 201, sent.text
    accepted = client.post(
        f"/api/v1/friends/requests/{sent.json()['id']}/accept",
        headers=auth(bob["access_token"]),
    )
    assert accepted.status_code == 200, accepted.text


def test_honor_requires_auth_hot_is_public(client: TestClient) -> None:
    assert client.get("/api/v1/honor").status_code == 401
    assert client.put("/api/v1/honor/sync", json={}).status_code == 401
    assert client.get("/api/v1/honor/friends").status_code == 401
    hot = client.get("/api/v1/honor/hot")
    assert hot.status_code == 200
    body = hot.json()
    assert "season" in body
    assert body["entries"] == []


def test_honor_friends_board_order_and_hot_public(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    carol = register(client, "carol")
    _befriend(client, alice, bob)

    alice_sync = client.put(
        "/api/v1/honor/sync",
        headers=auth(alice["access_token"]),
        json={
            "skins_taken": 2,
            "birdies": 1,
            "round_count": 1,
            "friend_count": 1,
            "challenge_count": 0,
        },
    )
    assert alice_sync.status_code == 200, alice_sync.text
    alice_body = alice_sync.json()
    assert "tally" in alice_body
    assert "tags" in alice_body
    assert "stats" in alice_body
    assert alice_body["stats"]["tally"] == alice_body["tally"]
    assert alice_body["tally"] >= 1 + 1 + 2 + 1  # rounds + friends + skins + birdies
    regular = next(t for t in alice_body["tags"] if t["id"] == "regular")
    assert regular["earned"] is True
    assert regular["metal"] in {"bronze", "silver", "gold", "platinum"}
    member = next(t for t in alice_body["tags"] if t["id"] == "member")
    assert member["earned"] is True
    plate = next(t for t in alice_body["tags"] if t["id"] == "season_plate")
    assert plate["earned"] is False
    assert plate["locked"] is True

    bob_sync = client.put(
        "/api/v1/honor/sync",
        headers=auth(bob["access_token"]),
        json={"skins_taken": 10, "birdies": 0, "round_count": 0, "friend_count": 1},
    )
    assert bob_sync.status_code == 200, bob_sync.text
    assert bob_sync.json()["tally"] > alice_body["tally"]

    carol_sync = client.put(
        "/api/v1/honor/sync",
        headers=auth(carol["access_token"]),
        json={"skins_taken": 50, "birdies": 0},
    )
    assert carol_sync.status_code == 200, carol_sync.text

    friends = client.get(
        "/api/v1/honor/friends",
        headers=auth(alice["access_token"]),
    )
    assert friends.status_code == 200, friends.text
    entries = friends.json()["entries"]
    names = [row["username"] for row in entries]
    assert "alice" in names
    assert "bob" in names
    assert "carol" not in names
    assert names[0] == "bob"
    assert names[1] == "alice"
    assert entries[0]["rank"] == 1
    assert entries[0]["tally"] >= entries[1]["tally"]
    assert "tags" in entries[0]
    assert "regular_metal" in entries[0]

    stranger = client.get(
        "/api/v1/honor/friends",
        headers=auth(carol["access_token"]),
    )
    assert stranger.status_code == 200
    stranger_names = [row["username"] for row in stranger.json()["entries"]]
    assert stranger_names == ["carol"]
    assert "alice" not in stranger_names
    assert "bob" not in stranger_names

    hot = client.get("/api/v1/honor/hot")
    assert hot.status_code == 200
    hot_body = hot.json()
    assert "season" in hot_body
    hot_names = [row["username"] for row in hot_body["entries"]]
    assert "carol" in hot_names
    assert "bob" in hot_names
    assert "alice" in hot_names
    assert hot_names[0] == "carol"
    for row in hot_body["entries"]:
        assert set(row.keys()) == HOT_ENTRY_KEYS
        for banned in WALLET_FIELDS:
            assert banned not in row
        assert "@" not in row["username"] or True
        assert "email" not in row
        assert "token_balance" not in row
        assert "scores" not in row

    own = client.get("/api/v1/honor", headers=auth(alice["access_token"]))
    assert own.status_code == 200
    snap = own.json()
    for key in ("season", "stats", "tags", "earned_count", "tag_total", "tally"):
        assert key in snap
    assert "rank_friends" in snap
