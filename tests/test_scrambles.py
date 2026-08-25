from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import auth, register


def tee_off(client: TestClient, token: str, host_team_index: int = 0):
    return client.post(
        "/api/v1/scrambles",
        headers=auth(token),
        json={
            "course_name": "Pebble Beach",
            "num_holes": 9,
            "wager_amount": 5,
            "host_team_index": host_team_index,
            "teams": [
                {"name": "Birdies", "start_hole": 1},
                {"name": "Bogeys", "start_hole": 1},
            ],
        },
    )


def test_scramble_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/scrambles").status_code == 401
    assert client.post(
        "/api/v1/scrambles",
        json={
            "course_name": "Pebble",
            "num_holes": 9,
            "wager_amount": 5,
            "host_team_index": 0,
            "teams": [{"name": "A"}, {"name": "B"}],
        },
    ).status_code == 401


def test_tee_off_creates_join_code_and_locks_host_team(client: TestClient) -> None:
    alice = register(client, "alice")
    created = tee_off(client, alice["access_token"])
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "active"
    assert len(body["join_code"]) == 6
    assert body["deep_link"].endswith(body["join_code"])
    assert body["my_team_index"] == 0
    assert [t["name"] for t in body["teams"]] == ["Birdies", "Bogeys"]
    assert body["revision"] == 1


def test_join_with_code_and_preview(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    created = tee_off(client, alice["access_token"]).json()
    code = created["join_code"]

    preview = client.get(f"/api/v1/scrambles/by-code/{code}", headers=auth(bob["access_token"]))
    assert preview.status_code == 200
    assert preview.json()["course_name"] == "Pebble Beach"
    assert "holes" not in preview.json()
    assert preview.json()["teams"][0]["name"] == "Birdies"

    joined = client.post(
        "/api/v1/scrambles/join",
        headers=auth(bob["access_token"]),
        json={"code": code.lower(), "team_index": 1},
    )
    assert joined.status_code == 200, joined.text
    assert joined.json()["my_team_index"] == 1
    assert any(m["username"] == "bob" for m in joined.json()["teams"][1]["members"])

    missing = client.post(
        "/api/v1/scrambles/join",
        headers=auth(bob["access_token"]),
        json={"code": "ZZZZZZ", "team_index": 0},
    )
    assert missing.status_code == 404


def test_only_own_team_can_score_and_holes_stay_hidden(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    created = tee_off(client, alice["access_token"]).json()
    client.post(
        "/api/v1/scrambles/join",
        headers=auth(bob["access_token"]),
        json={"code": created["join_code"], "team_index": 1},
    )
    sid = created["id"]

    posted = client.post(
        f"/api/v1/scrambles/{sid}/scores",
        headers=auth(alice["access_token"]),
        json={"strokes": 4},
    )
    assert posted.status_code == 200, posted.text
    alice_view = posted.json()
    assert alice_view["holes"][0]["revealed"] is False
    assert alice_view["holes"][0]["posted"] == [True, False]
    assert alice_view["holes"][0]["scores"] == [4, None]
    assert alice_view["teams"][0]["scores"][0] == 4

    bob_view = client.get(f"/api/v1/scrambles/{sid}", headers=auth(bob["access_token"]))
    assert bob_view.json()["holes"][0]["scores"] == [None, None]
    assert bob_view.json()["holes"][0]["posted"] == [True, False]
    assert bob_view.json()["teams"][0]["scores"][0] is None

    assert (
        client.get(f"/api/v1/scrambles/{sid}", headers=auth(cara["access_token"])).status_code
        == 403
    )

    second = client.post(
        f"/api/v1/scrambles/{sid}/scores",
        headers=auth(alice["access_token"]),
        json={"strokes": 5, "hole": 1},
    )
    assert second.status_code == 409


def test_last_team_reveals_hole_and_settles_skin(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    created = tee_off(client, alice["access_token"]).json()
    client.post(
        "/api/v1/scrambles/join",
        headers=auth(bob["access_token"]),
        json={"code": created["join_code"], "team_index": 1},
    )
    sid = created["id"]
    client.post(
        f"/api/v1/scrambles/{sid}/scores",
        headers=auth(alice["access_token"]),
        json={"strokes": 4},
    )
    bob_post = client.post(
        f"/api/v1/scrambles/{sid}/scores",
        headers=auth(bob["access_token"]),
        json={"strokes": 5},
    )
    assert bob_post.status_code == 200, bob_post.text
    body = bob_post.json()
    assert body["holes"][0]["revealed"] is True
    assert body["holes"][0]["settled"] is True
    assert body["holes"][0]["scores"] == [4, 5]
    assert body["teams"][0]["skins_won"] == 5
    assert body["teams"][1]["skins_won"] == 0
    assert body["skin_results"][0]["winner_index"] == 0
    assert body["skin_results"][0]["carry"] is False
    assert body["skin_pot"] == 5
    assert body["revision"] > 1


def test_tie_carries_the_skin(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    created = tee_off(client, alice["access_token"]).json()
    client.post(
        "/api/v1/scrambles/join",
        headers=auth(bob["access_token"]),
        json={"code": created["join_code"], "team_index": 1},
    )
    sid = created["id"]
    client.post(
        f"/api/v1/scrambles/{sid}/scores",
        headers=auth(alice["access_token"]),
        json={"strokes": 4},
    )
    tied = client.post(
        f"/api/v1/scrambles/{sid}/scores",
        headers=auth(bob["access_token"]),
        json={"strokes": 4},
    )
    body = tied.json()
    assert body["skin_results"][0]["carry"] is True
    assert body["skin_pot"] == 10
    assert body["skin_stack"] == 2
    assert body["teams"][0]["skins_won"] == 0


def test_list_my_scrambles(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    created = tee_off(client, alice["access_token"]).json()
    mine = client.get("/api/v1/scrambles", headers=auth(alice["access_token"]))
    assert mine.status_code == 200
    assert len(mine.json()["scrambles"]) == 1
    empty = client.get("/api/v1/scrambles", headers=auth(bob["access_token"]))
    assert empty.json()["scrambles"] == []
    client.post(
        "/api/v1/scrambles/join",
        headers=auth(bob["access_token"]),
        json={"code": created["join_code"], "team_index": 1},
    )
    bob_list = client.get("/api/v1/scrambles", headers=auth(bob["access_token"]))
    assert len(bob_list.json()["scrambles"]) == 1
    assert bob_list.json()["scrambles"][0]["join_code"] == created["join_code"]
