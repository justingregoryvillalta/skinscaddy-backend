from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from app.models.wallet import TokenDirection, TokenLedger
from tests.helpers import auth, register
from tests.test_challenges import become_friends, create_challenge

PARS = [4] * 18


def _post_round(client: TestClient, token: str, *, holes: int = 18, course: str = "Pebble Beach"):
    scores = [4] * holes
    pars = [4] * holes
    return client.post(
        "/api/v1/rounds",
        headers=auth(token),
        json={
            "course_name": course,
            "num_holes": holes,
            "scores": scores,
            "pars": pars,
        },
    )


def _honor_rows(db_session, round_id: int) -> list[TokenLedger]:
    db_session.expire_all()
    return list(
        db_session.query(TokenLedger)
        .filter(TokenLedger.reference == f"honor:round:{round_id}")
        .all()
    )


def test_owner_delete_removes_card_and_reverses_honor(client: TestClient, db_session) -> None:
    user = register(client, "solo")
    created = _post_round(client, user["access_token"])
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]
    # finish 10 + 18 pars
    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 128

    deleted = client.delete(
        f"/api/v1/rounds/{round_id}",
        headers=auth(user["access_token"]),
    )
    assert deleted.status_code == 204, deleted.text

    listed = client.get("/api/v1/rounds", headers=auth(user["access_token"]))
    assert listed.status_code == 200
    assert listed.json()["rounds"] == []
    missing = client.get(
        f"/api/v1/rounds/{round_id}",
        headers=auth(user["access_token"]),
    )
    assert missing.status_code == 404

    wallet = client.get("/api/v1/wallet", headers=auth(user["access_token"]))
    assert wallet.json()["balance"] == 100
    rows = _honor_rows(db_session, round_id)
    credits = [row for row in rows if row.direction == TokenDirection.CREDIT]
    debits = [row for row in rows if row.direction == TokenDirection.DEBIT]
    assert len(credits) == 1
    assert len(debits) == 1
    assert credits[0].amount == 28
    assert debits[0].amount == 28
    assert debits[0].reference == f"honor:round:{round_id}"

    again = client.delete(
        f"/api/v1/rounds/{round_id}",
        headers=auth(user["access_token"]),
    )
    assert again.status_code == 404


def test_delete_requires_owner(client: TestClient) -> None:
    owner = register(client, "owner")
    other = register(client, "other")
    created = _post_round(client, owner["access_token"], holes=9)
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]

    anonymous = client.delete(f"/api/v1/rounds/{round_id}")
    assert anonymous.status_code == 401

    denied = client.delete(
        f"/api/v1/rounds/{round_id}",
        headers=auth(other["access_token"]),
    )
    assert denied.status_code == 403

    still = client.get("/api/v1/rounds", headers=auth(owner["access_token"]))
    assert [row["id"] for row in still.json()["rounds"]] == [round_id]
    missing = client.delete(
        "/api/v1/rounds/999999",
        headers=auth(owner["access_token"]),
    )
    assert missing.status_code == 404


def test_side_game_blocks_delete(client: TestClient, db_session) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    created = _post_round(client, alice["access_token"])
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]
    before = client.get("/api/v1/wallet", headers=auth(alice["access_token"])).json()["balance"]
    assert before == 128

    challenged = create_challenge(
        client,
        alice["access_token"],
        ["bob"],
        round_id,
        wager=10,
    )
    assert challenged.status_code == 201, challenged.text

    denied = client.delete(
        f"/api/v1/rounds/{round_id}",
        headers=auth(alice["access_token"]),
    )
    assert denied.status_code == 409
    assert "side game" in denied.json()["detail"].lower()

    listed = client.get("/api/v1/rounds", headers=auth(alice["access_token"]))
    assert [row["id"] for row in listed.json()["rounds"]] == [round_id]
    wallet = client.get("/api/v1/wallet", headers=auth(alice["access_token"]))
    assert wallet.json()["balance"] == before
    rows = _honor_rows(db_session, round_id)
    assert len(rows) == 1
    assert rows[0].direction == TokenDirection.CREDIT


def test_shared_chat_card_can_be_deleted(client: TestClient) -> None:
    alice = register(client, "alice")
    bob = register(client, "bob")
    become_friends(client, alice, bob)
    thread = client.post(
        "/api/v1/chats/direct",
        headers=auth(alice["access_token"]),
        json={"username": "bob"},
    )
    assert thread.status_code == 200, thread.text
    created = _post_round(client, alice["access_token"], holes=9)
    assert created.status_code == 201, created.text
    round_id = created.json()["id"]
    shared = client.post(
        f"/api/v1/chats/{thread.json()['id']}/messages",
        headers=auth(alice["access_token"]),
        json={"kind": "round", "round_id": round_id},
    )
    assert shared.status_code == 201, shared.text

    deleted = client.delete(
        f"/api/v1/rounds/{round_id}",
        headers=auth(alice["access_token"]),
    )
    assert deleted.status_code == 204, deleted.text
    listed = client.get("/api/v1/rounds", headers=auth(alice["access_token"]))
    assert listed.json()["rounds"] == []
    wallet = client.get("/api/v1/wallet", headers=auth(alice["access_token"]))
    assert wallet.json()["balance"] == 100


def test_reads_do_not_call_opengolf() -> None:
    import app.api.v1.rounds as rounds_api
    import app.api.v1.wallet as wallet_api
    import app.services.rounds as rounds_svc

    blob = " ".join(
        (
            inspect.getsource(rounds_api.get_rounds),
            inspect.getsource(rounds_api.get_one_round),
            inspect.getsource(wallet_api.read_wallet),
            inspect.getsource(wallet_api.read_history),
            inspect.getsource(rounds_svc.list_rounds),
            inspect.getsource(rounds_svc.get_round),
        )
    ).lower()
    assert "opengolf" not in blob
    assert "open_golf" not in blob
