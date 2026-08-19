from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.photo import Photo

TINY_JPEG = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
)


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


def make_challenge(client: TestClient, alice: dict, bob: dict) -> int:
    client.post(
        "/api/v1/wallet/credit",
        headers=auth(alice["access_token"]),
        json={"amount": 20, "source": "reward", "reason": "bank"},
    )
    rnd = client.post(
        "/api/v1/rounds",
        headers=auth(alice["access_token"]),
        json={"course_name": "Pebble Beach", "num_holes": 9, "scores": [4] * 9},
    )
    created = client.post(
        "/api/v1/challenges",
        headers=auth(alice["access_token"]),
        json={
            "usernames": [bob["user"]["username"]],
            "round_id": rnd.json()["id"],
            "wager_amount": 0,
            "weeks": 1,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def upload(
    client: TestClient,
    token: str,
    *,
    kind: str,
    challenge_id: int | None = None,
    recipients: str | None = None,
    hole: int | None = None,
    caption: str | None = None,
    expires_in_days: int = 7,
    payload: bytes = TINY_JPEG,
    filename: str = "shot.jpg",
    content_type: str = "image/jpeg",
):
    data: dict = {"kind": kind, "expires_in_days": str(expires_in_days)}
    if challenge_id is not None:
        data["challenge_id"] = str(challenge_id)
    if recipients is not None:
        data["recipients"] = recipients
    if hole is not None:
        data["hole"] = str(hole)
    if caption is not None:
        data["caption"] = caption
    return client.post(
        "/api/v1/photos",
        headers=auth(token),
        files={"file": (filename, payload, content_type)},
        data=data,
    )


def test_photo_routes_require_auth(client: TestClient) -> None:
    assert client.get("/api/v1/photos").status_code == 401
    assert client.get("/api/v1/photos/1/file").status_code == 401
    assert (
        client.post(
            "/api/v1/photos",
            files={"file": ("shot.jpg", TINY_JPEG, "image/jpeg")},
            data={"kind": "prop", "recipients": "bob"},
        ).status_code
        == 401
    )


def test_challenge_photo_view_once(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    cid = make_challenge(client, alice, bob)

    created = upload(
        client,
        alice["access_token"],
        kind="challenge",
        challenge_id=cid,
        caption="Beat this lie",
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["kind"] == "challenge"
    assert body["available"] is True
    assert body["url"] == f"/api/v1/photos/{body['id']}/file"
    assert body["sender"]["username"] == "alice"
    assert [row["user"]["username"] for row in body["recipients"]] == ["bob"]

    sender_view = client.get(body["url"], headers=auth(alice["access_token"]))
    assert sender_view.status_code == 200
    assert sender_view.content == TINY_JPEG
    still = client.get(body["url"], headers=auth(alice["access_token"]))
    assert still.status_code == 200

    first = client.get(body["url"], headers=auth(bob["access_token"]))
    assert first.status_code == 200
    assert first.content == TINY_JPEG
    assert first.headers["content-type"].startswith("image/jpeg")

    gone = client.get(body["url"], headers=auth(bob["access_token"]))
    assert gone.status_code == 410
    sender_gone = client.get(body["url"], headers=auth(alice["access_token"]))
    assert sender_gone.status_code == 410

    meta = client.get(f"/api/v1/photos/{body['id']}", headers=auth(alice["access_token"]))
    assert meta.status_code == 200
    assert meta.json()["status"] == "consumed"
    assert meta.json()["available"] is False


def test_stranger_cannot_view_challenge_photo(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    become_friends(client, alice, bob)
    cid = make_challenge(client, alice, bob)
    photo = upload(client, alice["access_token"], kind="challenge", challenge_id=cid)
    assert (
        client.get(photo.json()["url"], headers=auth(cara["access_token"])).status_code
        == 403
    )


def test_prop_photo_to_friend_and_reject_non_friend(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    become_friends(client, alice, bob)

    ok = upload(
        client,
        alice["access_token"],
        kind="prop",
        recipients="bob",
        hole=7,
        caption="Closest to pin",
        expires_in_days=14,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["kind"] == "prop"
    assert ok.json()["hole"] == 7
    assert ok.json()["expires_in_days"] == 14

    denied = upload(
        client,
        alice["access_token"],
        kind="prop",
        recipients="cara",
        hole=3,
    )
    assert denied.status_code == 403


def test_rejects_bad_file_and_expiry(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)

    not_image = upload(
        client,
        alice["access_token"],
        kind="prop",
        recipients="bob",
        payload=b"not-an-image",
        filename="note.txt",
        content_type="text/plain",
    )
    assert not_image.status_code == 400

    bad_days = upload(
        client,
        alice["access_token"],
        kind="prop",
        recipients="bob",
        expires_in_days=3,
    )
    assert bad_days.status_code == 400


def test_expired_photo_is_deleted(
    client: TestClient,
    db_session: Session,
) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    created = upload(
        client,
        alice["access_token"],
        kind="prop",
        recipients="bob",
    )
    pid = created.json()["id"]

    row = db_session.get(Photo, pid)
    assert row is not None
    disk = Path(os.environ["PHOTO_DIR"]) / row.storage_name
    assert disk.is_file()
    row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    gone = client.get(
        f"/api/v1/photos/{pid}/file",
        headers=auth(bob["access_token"]),
    )
    assert gone.status_code == 410
    assert not disk.is_file()


def test_list_only_shows_photos_you_can_access(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    cara = register(client, "cara")
    become_friends(client, alice, bob)
    become_friends(client, alice, cara)
    upload(client, alice["access_token"], kind="prop", recipients="bob", hole=1)
    upload(client, alice["access_token"], kind="prop", recipients="cara", hole=2)

    bob_list = client.get("/api/v1/photos", headers=auth(bob["access_token"]))
    assert bob_list.status_code == 200
    assert len(bob_list.json()["photos"]) == 1
    assert bob_list.json()["photos"][0]["hole"] == 1

    alice_list = client.get("/api/v1/photos", headers=auth(alice["access_token"]))
    assert len(alice_list.json()["photos"]) == 2
