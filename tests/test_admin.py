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


def test_admin_routes_require_justinv(client: TestClient) -> None:
    bob = register(client, "bob")
    assert client.get("/api/v1/admin/users").status_code == 401
    denied = client.get("/api/v1/admin/users", headers=auth(bob["access_token"]))
    assert denied.status_code == 403


def test_admin_lists_and_adjusts_users(client: TestClient) -> None:
    admin = register(client, "justinv")
    bob = register(client, "bob")
    listed = client.get("/api/v1/admin/users", headers=auth(admin["access_token"]))
    assert listed.status_code == 200
    names = {row["username"] for row in listed.json()["users"]}
    assert names == {"justinv", "bob"}
    bob_row = next(row for row in listed.json()["users"] if row["username"] == "bob")
    assert bob_row["token_balance"] == 100
    assert bob_row["is_disabled"] is False

    updated = client.put(
        f"/api/v1/admin/users/{bob['user']['id']}/tokens",
        headers=auth(admin["access_token"]),
        json={"balance": 250},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["token_balance"] == 250
    wallet = client.get("/api/v1/wallet", headers=auth(bob["access_token"]))
    assert wallet.json()["balance"] == 250


def test_admin_rename_flag_and_delete(client: TestClient) -> None:
    admin = register(client, "justinv")
    bob = register(client, "bob")
    headers = auth(admin["access_token"])

    renamed = client.put(
        f"/api/v1/admin/users/{bob['user']['id']}/username",
        headers=headers,
        json={"username": "bobby"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["username"] == "bobby"

    flagged = client.post(
        f"/api/v1/admin/users/{bob['user']['id']}/flag",
        headers=headers,
        json={"disabled": True},
    )
    assert flagged.status_code == 200
    assert flagged.json()["is_disabled"] is True

    login = client.post(
        "/api/v1/auth/login",
        json={"username": "bobby", "password": "password123"},
    )
    assert login.status_code == 403

    client.post(
        f"/api/v1/admin/users/{bob['user']['id']}/flag",
        headers=headers,
        json={"disabled": False},
    )
    ok = client.post(
        "/api/v1/auth/login",
        json={"username": "bobby", "password": "password123"},
    )
    assert ok.status_code == 200

    deleted = client.delete(
        f"/api/v1/admin/users/{bob['user']['id']}",
        headers=headers,
    )
    assert deleted.status_code == 204
    listed = client.get("/api/v1/admin/users", headers=headers)
    assert [row["username"] for row in listed.json()["users"]] == ["justinv"]


def test_admin_can_adjust_own_account(client: TestClient) -> None:
    admin = register(client, "justinv")
    headers = auth(admin["access_token"])
    uid = admin["user"]["id"]

    tokens = client.put(
        f"/api/v1/admin/users/{uid}/tokens",
        headers=headers,
        json={"balance": 500},
    )
    assert tokens.status_code == 200, tokens.text
    assert tokens.json()["token_balance"] == 500
    assert tokens.json()["username"] == "justinv"

    zero = client.put(
        f"/api/v1/admin/users/{uid}/tokens",
        headers=headers,
        json={"balance": 0},
    )
    assert zero.status_code == 200, zero.text
    assert zero.json()["token_balance"] == 0

    restored = client.put(
        f"/api/v1/admin/users/{uid}/tokens",
        headers=headers,
        json={"balance": 250},
    )
    assert restored.status_code == 200
    assert restored.json()["token_balance"] == 250

    wallet = client.get("/api/v1/wallet", headers=headers)
    assert wallet.json()["balance"] == 250

    renamed = client.put(
        f"/api/v1/admin/users/{uid}/username",
        headers=headers,
        json={"username": "Justinv"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["username"] == "Justinv"

    listed = client.get("/api/v1/admin/users", headers=headers)
    assert listed.status_code == 200
    me = next(row for row in listed.json()["users"] if row["id"] == uid)
    assert me["username"] == "Justinv"
    assert me["token_balance"] == 250


def test_admin_list_includes_new_signups(client: TestClient) -> None:
    admin = register(client, "justinv")
    headers = auth(admin["access_token"])
    first = client.get("/api/v1/admin/users", headers=headers)
    assert first.status_code == 200
    assert {row["username"] for row in first.json()["users"]} == {"justinv"}

    newbie = register(client, "newbie")
    assert newbie["user"]["token_balance"] == 100

    listed = client.get("/api/v1/admin/users", headers=headers)
    assert listed.status_code == 200
    rows = listed.json()["users"]
    names = {row["username"] for row in rows}
    assert names == {"justinv", "newbie"}
    found = next(row for row in rows if row["username"] == "newbie")
    assert found["id"] == newbie["user"]["id"]
    assert found["token_balance"] == 100
