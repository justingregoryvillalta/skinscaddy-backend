from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import auth, register


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


def play(
    client: TestClient,
    token: str,
    *,
    course: str = "Pebble Beach",
    hole: int = 3,
    privacy: str = "full",
    scores: list[int] | None = None,
    total: int | None = None,
    mode: str = "solo",
):
    body = {
        "state": "playing",
        "mode": mode,
        "course_name": course,
        "hole": hole,
        "holes_completed": max(0, hole - 1),
        "num_holes": 9,
        "privacy": privacy,
        "allow_join": True,
    }
    if scores is not None:
        body["scores"] = scores
    if total is not None:
        body["total"] = total
    return client.put("/api/v1/status", headers=auth(token), json=body)


def test_feed_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/status").status_code == 401
    assert client.get("/api/v1/feed").status_code == 401
    assert client.put("/api/v1/status", json={"state": "idle"}).status_code == 401


def test_own_status_defaults_to_idle(client: TestClient) -> None:
    alice = register(client, "alice")
    response = client.get("/api/v1/status", headers=auth(alice["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "idle"
    assert body["user"]["username"] == "alice"
    assert body["hole"] is None


def test_playing_appears_in_friends_feed_only(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    become_friends(client, alice, bob)

    updated = play(
        client,
        alice["access_token"],
        hole=4,
        privacy="full",
        scores=[4, 5, 3],
        total=12,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["state"] == "playing"
    assert updated.json()["mode_label"] == "Solo 2.0"
    assert updated.json()["hole"] == 4

    bob_feed = client.get("/api/v1/feed", headers=auth(bob["access_token"]))
    assert bob_feed.status_code == 200
    live = bob_feed.json()["live"]
    assert len(live) == 1
    assert live[0]["user"]["username"] == "alice"
    assert live[0]["course_name"] == "Pebble Beach"
    assert live[0]["hole"] == 4
    assert live[0]["show_scores"] is True
    assert live[0]["scores"] == [4, 5, 3]
    assert live[0]["total"] == 12

    kinds = [row["kind"] for row in bob_feed.json()["activity"]]
    assert "started_round" in kinds

    cara_feed = client.get("/api/v1/feed", headers=auth(cara["access_token"]))
    assert cara_feed.json()["live"] == []
    assert cara_feed.json()["activity"] == []

    alice_feed = client.get("/api/v1/feed", headers=auth(alice["access_token"]))
    assert alice_feed.json()["live"] == []


def test_limited_privacy_hides_scores_but_shows_hole(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    play(
        client,
        alice["access_token"],
        hole=6,
        privacy="limited",
        scores=[4, 4, 5, 3, 4],
        total=20,
    )

    feed = client.get("/api/v1/feed", headers=auth(bob["access_token"]))
    live = feed.json()["live"][0]
    assert live["privacy"] == "limited"
    assert live["hole"] == 6
    assert live["course_name"] == "Pebble Beach"
    assert live["show_scores"] is False
    assert live["scores"] == []
    assert live["total"] is None

    own = client.get("/api/v1/status", headers=auth(alice["access_token"]))
    assert own.json()["show_scores"] is True
    assert own.json()["scores"] == [4, 4, 5, 3, 4]


def test_idle_removes_from_live_and_finished_writes_activity(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    play(client, alice["access_token"], hole=2)

    idle = client.put(
        "/api/v1/status",
        headers=auth(alice["access_token"]),
        json={"state": "idle"},
    )
    assert idle.status_code == 200
    assert idle.json()["state"] == "idle"
    assert client.get("/api/v1/feed", headers=auth(bob["access_token"])).json()["live"] == []

    play(client, alice["access_token"], hole=9, scores=[4] * 9, total=36)
    finished = client.put(
        "/api/v1/status",
        headers=auth(alice["access_token"]),
        json={
            "state": "finished",
            "mode": "skins",
            "course_name": "Pebble Beach",
            "total": 36,
            "scores": [4] * 9,
            "privacy": "full",
        },
    )
    assert finished.status_code == 200
    feed = client.get("/api/v1/feed", headers=auth(bob["access_token"]))
    assert feed.json()["live"] == []
    kinds = [row["kind"] for row in feed.json()["activity"]]
    assert "finished_round" in kinds
    assert "started_round" in kinds


def test_playing_requires_course_and_mode(client: TestClient) -> None:
    alice = register(client, "alice")
    response = client.put(
        "/api/v1/status",
        headers=auth(alice["access_token"]),
        json={"state": "playing"},
    )
    assert response.status_code == 422


def test_won_skins_event_shows_in_friend_activity(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    posted = client.post(
        "/api/v1/feed/events",
        headers=auth(alice["access_token"]),
        json={
            "kind": "won_skins",
            "course_name": "Augusta National",
            "mode": "skins",
        },
    )
    assert posted.status_code == 201, posted.text
    assert "won skins" in posted.json()["summary"].lower()

    feed = client.get("/api/v1/feed", headers=auth(bob["access_token"]))
    assert feed.json()["activity"][0]["kind"] == "won_skins"
    assert feed.json()["activity"][0]["course_name"] == "Augusta National"
    assert feed.json()["activity"][0]["mode_label"] == "Skins"
