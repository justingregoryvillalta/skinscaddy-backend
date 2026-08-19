from __future__ import annotations

from fastapi.testclient import TestClient


def register(client: TestClient, username: str, password: str = "password123") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert response.status_code == 201, response.text
    return response.json()


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def send_request(client: TestClient, token: str, username: str):
    return client.post(
        "/api/v1/friends/requests",
        headers=auth(token),
        json={"username": username},
    )


def test_friend_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/friends").status_code == 401
    assert client.get("/api/v1/friends/requests/incoming").status_code == 401
    assert client.get("/api/v1/friends/requests/outgoing").status_code == 401
    assert client.post("/api/v1/friends/requests", json={"username": "bob"}).status_code == 401
    assert client.post("/api/v1/friends/requests/1/accept").status_code == 401
    assert client.post("/api/v1/friends/requests/1/decline").status_code == 401


def test_send_friend_request_by_username(client: TestClient) -> None:
    alice = register(client, "alice")
    register(client, "bob")

    response = send_request(client, alice["access_token"], "Bob")
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending"
    assert body["requester"]["username"] == "alice"
    assert body["addressee"]["username"] == "bob"


def test_cannot_friend_self(client: TestClient) -> None:
    alice = register(client, "alice")
    response = send_request(client, alice["access_token"], "alice")
    assert response.status_code == 400
    assert "yourself" in response.json()["detail"].lower()


def test_unknown_username_is_not_found(client: TestClient) -> None:
    alice = register(client, "alice")
    response = send_request(client, alice["access_token"], "nobody")
    assert response.status_code == 404


def test_duplicate_and_reverse_requests_are_rejected(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")

    assert send_request(client, alice["access_token"], "bob").status_code == 201

    duplicate = send_request(client, alice["access_token"], "bob")
    assert duplicate.status_code == 409

    reverse = send_request(client, bob["access_token"], "alice")
    assert reverse.status_code == 409


def test_incoming_and_outgoing_pending_lists(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    send_request(client, alice["access_token"], "bob")

    outgoing = client.get(
        "/api/v1/friends/requests/outgoing",
        headers=auth(alice["access_token"]),
    )
    assert outgoing.status_code == 200
    out_rows = outgoing.json()["requests"]
    assert len(out_rows) == 1
    assert out_rows[0]["addressee"]["username"] == "bob"

    incoming = client.get(
        "/api/v1/friends/requests/incoming",
        headers=auth(bob["access_token"]),
    )
    assert incoming.status_code == 200
    in_rows = incoming.json()["requests"]
    assert len(in_rows) == 1
    assert in_rows[0]["requester"]["username"] == "alice"

    assert (
        client.get(
            "/api/v1/friends/requests/incoming",
            headers=auth(alice["access_token"]),
        ).json()["requests"]
        == []
    )


def test_accept_friend_request(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    request_id = send_request(client, alice["access_token"], "bob").json()["id"]

    accepted = client.post(
        f"/api/v1/friends/requests/{request_id}/accept",
        headers=auth(bob["access_token"]),
    )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    alice_friends = client.get("/api/v1/friends", headers=auth(alice["access_token"]))
    bob_friends = client.get("/api/v1/friends", headers=auth(bob["access_token"]))
    assert alice_friends.status_code == 200
    assert [row["user"]["username"] for row in alice_friends.json()["friends"]] == ["bob"]
    assert [row["user"]["username"] for row in bob_friends.json()["friends"]] == ["alice"]

    assert (
        client.get(
            "/api/v1/friends/requests/outgoing",
            headers=auth(alice["access_token"]),
        ).json()["requests"]
        == []
    )
    assert (
        client.get(
            "/api/v1/friends/requests/incoming",
            headers=auth(bob["access_token"]),
        ).json()["requests"]
        == []
    )


def test_decline_friend_request(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    request_id = send_request(client, alice["access_token"], "bob").json()["id"]

    declined = client.post(
        f"/api/v1/friends/requests/{request_id}/decline",
        headers=auth(bob["access_token"]),
    )
    assert declined.status_code == 200
    assert declined.json()["status"] == "declined"

    assert client.get("/api/v1/friends", headers=auth(alice["access_token"])).json()["friends"] == []
    assert (
        client.get(
            "/api/v1/friends/requests/incoming",
            headers=auth(bob["access_token"]),
        ).json()["requests"]
        == []
    )


def test_only_addressee_can_respond(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    charlie = register(client, "charlie")
    request_id = send_request(client, alice["access_token"], "bob").json()["id"]

    assert (
        client.post(
            f"/api/v1/friends/requests/{request_id}/accept",
            headers=auth(alice["access_token"]),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/friends/requests/{request_id}/decline",
            headers=auth(charlie["access_token"]),
        ).status_code
        == 403
    )


def test_already_friends_cannot_request_again(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    request_id = send_request(client, alice["access_token"], "bob").json()["id"]
    client.post(
        f"/api/v1/friends/requests/{request_id}/accept",
        headers=auth(bob["access_token"]),
    )

    again = send_request(client, bob["access_token"], "alice")
    assert again.status_code == 409
    assert "already friends" in again.json()["detail"].lower()


def test_can_resend_after_decline(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    request_id = send_request(client, alice["access_token"], "bob").json()["id"]
    client.post(
        f"/api/v1/friends/requests/{request_id}/decline",
        headers=auth(bob["access_token"]),
    )

    again = send_request(client, alice["access_token"], "bob")
    assert again.status_code == 201
    assert again.json()["status"] == "pending"
    assert again.json()["id"] == request_id


def test_cannot_accept_missing_or_already_handled_request(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    request_id = send_request(client, alice["access_token"], "bob").json()["id"]

    missing = client.post(
        "/api/v1/friends/requests/999/accept",
        headers=auth(bob["access_token"]),
    )
    assert missing.status_code == 404

    client.post(
        f"/api/v1/friends/requests/{request_id}/accept",
        headers=auth(bob["access_token"]),
    )
    again = client.post(
        f"/api/v1/friends/requests/{request_id}/accept",
        headers=auth(bob["access_token"]),
    )
    assert again.status_code == 409
