from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import auth, register
from tests.test_friends import send_request


def _befriend(client: TestClient, alice: dict, bob: dict) -> None:
    request_id = send_request(client, alice["access_token"], bob["user"]["username"]).json()["id"]
    accepted = client.post(
        f"/api/v1/friends/requests/{request_id}/accept",
        headers=auth(bob["access_token"]),
    )
    assert accepted.status_code == 200, accepted.text


def test_chat_requires_auth(client: TestClient) -> None:
    assert client.get("/api/v1/chats").status_code == 401
    assert client.post("/api/v1/chats/direct", json={"username": "bob"}).status_code == 401


def test_direct_chat_requires_friendship(client: TestClient) -> None:
    alice = register(client, "alice")
    register(client, "bob")
    blocked = client.post(
        "/api/v1/chats/direct",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    assert blocked.status_code == 403


def test_group_chat_round_snapshot_and_privacy(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    _befriend(client, alice, bob)

    group = client.post(
        "/api/v1/chats/groups",
        headers=auth(alice["access_token"]),
        json={"title": "Saturday crew", "usernames": ["bob"]},
    )
    assert group.status_code == 201, group.text
    thread_id = group.json()["id"]
    names = {m["user"]["username"] for m in group.json()["members"]}
    assert names == {"alice", "bob"}

    outsider = client.get(
        f"/api/v1/chats/{thread_id}/messages",
        headers=auth(cara["access_token"]),
    )
    assert outsider.status_code == 403

    texted = client.post(
        f"/api/v1/chats/{thread_id}/messages",
        headers=auth(alice["access_token"]),
        json={"text": "tee times at 8"},
    )
    assert texted.status_code == 201, texted.text
    assert texted.json()["kind"] == "text"
    assert texted.json()["user"]["username"] == "alice"

    created = client.post(
        "/api/v1/rounds",
        headers=auth(alice["access_token"]),
        json={
            "course_name": "Pebble Beach",
            "num_holes": 9,
            "scores": [4, 5, 3, 4, 5, 4, 3, 4, 5],
            "pars": [4, 4, 3, 4, 5, 4, 3, 4, 5],
        },
    )
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]

    shared = client.post(
        f"/api/v1/chats/{thread_id}/messages",
        headers=auth(alice["access_token"]),
        json={
            "kind": "round",
            "round_id": round_id,
            "snapshot": {
                "skins_result": "2 skins · 30 chips",
                "players": ["alice", "bob"],
            },
        },
    )
    assert shared.status_code == 201, shared.text
    body = shared.json()
    assert body["kind"] == "round"
    assert body["round_id"] == round_id
    snap = body["snapshot"]
    assert snap["course"] == "Pebble Beach"
    assert snap["score"] == 37
    assert "2 skins" in snap["skins_result"]
    assert "alice" in snap["summary"].lower() or "Pebble" in snap["summary"]

    bob_view = client.get(
        f"/api/v1/chats/{thread_id}/messages",
        headers=auth(bob["access_token"]),
    )
    assert bob_view.status_code == 200
    kinds = [m["kind"] for m in bob_view.json()["messages"]]
    assert kinds == ["text", "round"]

    listed = client.get("/api/v1/chats", headers=auth(bob["access_token"]))
    assert listed.status_code == 200
    assert listed.json()["threads"][0]["id"] == thread_id


def test_direct_chat_reuses_thread(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    _befriend(client, alice, bob)
    first = client.post(
        "/api/v1/chats/direct",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    second = client.post(
        "/api/v1/chats/direct",
        headers=auth(bob["access_token"]),
        json={"username": "alice"},
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["kind"] == "direct"

    at_user = client.post(
        "/api/v1/chats/direct",
        headers=auth(alice["access_token"]),
        json={"username": "@bob"},
    )
    assert at_user.status_code == 200, at_user.text
    assert at_user.json()["id"] == first.json()["id"]

    texted = client.post(
        f"/api/v1/chats/{first.json()['id']}/messages",
        headers=auth(alice["access_token"]),
        json={"text": "see you on 1"},
    )
    assert texted.status_code == 201, texted.text
    listed = client.get(
        f"/api/v1/chats/{first.json()['id']}/messages",
        headers=auth(bob["access_token"]),
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["messages"][0]["text"] == "see you on 1"

def test_openapi_includes_chat_routes(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    paths = spec.get("paths") or {}
    assert "/api/v1/chats" in paths
    assert "/api/v1/chats/direct" in paths
    assert spec["info"]["version"] == "0.1.6"
    health = client.get("/health").json()
    assert health.get("chats") is True
    assert health.get("version") == "0.1.6"

