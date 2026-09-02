from __future__ import annotations

from fastapi.testclient import TestClient

from tests.helpers import auth, register
from tests.test_chat import _befriend
from tests.test_challenges import add_round, become_friends, create_challenge, credit


def _register_token(client: TestClient, token: str, fcm: str = "fcm-token-android-1"):
    return client.post(
        "/api/v1/devices/fcm",
        headers=auth(token),
        json={"token": fcm, "platform": "android"},
    )


def test_device_token_requires_auth(client: TestClient) -> None:
    assert client.post("/api/v1/devices/fcm", json={"token": "abc123456"}).status_code == 401


def test_register_and_reassign_device_token(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    first = _register_token(client, alice["access_token"], "shared-phone-token-1")
    assert first.status_code == 200, first.text
    assert first.json()["ok"] is True
    moved = _register_token(client, bob["access_token"], "shared-phone-token-1")
    assert moved.status_code == 200, moved.text
    gone = client.delete(
        "/api/v1/devices/fcm",
        headers=auth(alice["access_token"]),
        params={"token": "shared-phone-token-1"},
    )
    assert gone.status_code == 200


def test_chat_notifies_other_member_not_sender(client: TestClient, monkeypatch) -> None:
    sent: list[dict] = []

    def fake_deliver(**kwargs):
        sent.append(kwargs)
        return "ok"

    monkeypatch.setattr("app.services.push.fcm_configured", lambda: True)
    monkeypatch.setattr("app.services.push.deliver_fcm", fake_deliver)

    alice = register(client, "alice")
    bob = register(client, "bob")
    _befriend(client, alice, bob)
    _register_token(client, bob["access_token"], "bob-fcm-token-xx")
    _register_token(client, alice["access_token"], "alice-fcm-token-xx")

    thread = client.post(
        "/api/v1/chats/direct",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    assert thread.status_code == 200, thread.text
    tid = thread.json()["id"]
    posted = client.post(
        f"/api/v1/chats/{tid}/messages",
        headers=auth(alice["access_token"]),
        json={"text": "tee times at 8"},
    )
    assert posted.status_code == 201, posted.text
    tokens = {row["token"] for row in sent}
    assert "bob-fcm-token-xx" in tokens
    assert "alice-fcm-token-xx" not in tokens
    assert sent[0]["title"].startswith("Chat")
    assert "tee times" in sent[0]["body"]
    assert sent[0]["data"]["kind"] == "chat"
    assert sent[0]["data"]["screen"] == "group_chat"


def test_chat_succeeds_when_push_raises(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.services.push.fcm_configured", lambda: True)

    def boom(**kwargs):
        raise RuntimeError("fcm down")

    monkeypatch.setattr("app.services.push.deliver_fcm", boom)
    alice = register(client, "alice")
    bob = register(client, "bob")
    _befriend(client, alice, bob)
    _register_token(client, bob["access_token"], "bob-fcm-token-yy")
    thread = client.post(
        "/api/v1/chats/direct",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    tid = thread.json()["id"]
    posted = client.post(
        f"/api/v1/chats/{tid}/messages",
        headers=auth(alice["access_token"]),
        json={"text": "still delivered"},
    )
    assert posted.status_code == 201, posted.text
    assert posted.json()["text"] == "still delivered"


def test_friend_request_notifies_addressee(client: TestClient, monkeypatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr("app.services.push.fcm_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.push.deliver_fcm",
        lambda **kwargs: sent.append(kwargs) or "ok",
    )
    alice = register(client, "alice")
    bob = register(client, "bob")
    _register_token(client, bob["access_token"], "bob-friend-fcm")
    req = client.post(
        "/api/v1/friends/requests",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    assert req.status_code == 201, req.text
    assert len(sent) == 1
    assert sent[0]["data"]["kind"] == "friend"
    assert sent[0]["data"]["screen"] == "activity"
    assert "@alice" in sent[0]["body"]


def test_challenge_notifies_opponent(client: TestClient, monkeypatch) -> None:
    sent: list[dict] = []
    monkeypatch.setattr("app.services.push.fcm_configured", lambda: True)
    monkeypatch.setattr(
        "app.services.push.deliver_fcm",
        lambda **kwargs: sent.append(kwargs) or "ok",
    )
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    credit(client, alice["access_token"], 50)
    credit(client, bob["access_token"], 50)
    _register_token(client, bob["access_token"], "bob-challenge-fcm")
    rnd = add_round(client, alice["access_token"])
    created = create_challenge(client, alice["access_token"], ["bob"], rnd["id"], wager=10)
    assert created.status_code == 201, created.text
    assert any(row["data"]["kind"] == "challenge" for row in sent)
    assert any("challenged you" in row["body"] for row in sent)
    for row in sent:
        assert "token" not in row["body"].lower()
        assert "score" not in row["body"].lower()
