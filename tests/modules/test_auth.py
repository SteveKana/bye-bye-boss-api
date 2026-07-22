from __future__ import annotations

from httpx import AsyncClient

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"


async def test_register_and_login_and_me(client: AsyncClient) -> None:
    payload = {"email": "a@b.com", "password": "supersecret", "full_name": "A"}
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "a@b.com"
    assert body["isadmin"] is False
    assert body["subscription"] == "standard"
    assert body["last_rescoring_time"] is None

    r = await client.post(LOGIN, json={"email": "a@b.com", "password": "supersecret"})
    assert r.status_code == 200
    tokens = r.json()
    assert tokens["token_type"] == "bearer"

    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.com"


async def test_duplicate_email_conflicts(client: AsyncClient) -> None:
    payload = {"email": "dup@b.com", "password": "supersecret"}
    assert (await client.post(REGISTER, json=payload)).status_code == 201
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"


async def test_bad_credentials(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "c@b.com", "password": "supersecret"})
    r = await client.post(LOGIN, json={"email": "c@b.com", "password": "wrong"})
    assert r.status_code == 401


async def test_refresh_token_rejected_as_access(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "d@b.com", "password": "supersecret"})
    tokens = (
        await client.post(LOGIN, json={"email": "d@b.com", "password": "supersecret"})
    ).json()
    # A refresh token must not authenticate normal endpoints.
    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )
    assert r.status_code == 401

    # But it works at the refresh endpoint.
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_me_requires_auth(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/me")
    assert r.status_code == 401


RESET_REQUEST = "/api/v1/auth/reset-password/request"
RESET_CONFIRM = "/api/v1/auth/reset-password/confirm"


async def test_password_reset_flow(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "r@b.com", "password": "supersecret"})

    r = await client.post(RESET_REQUEST, json={"email": "r@b.com"})
    assert r.status_code == 202
    token = r.json()["reset_token"]  # surfaced in debug (test env)
    assert token

    r = await client.post(
        RESET_CONFIRM, json={"token": token, "new_password": "brandnewpass"}
    )
    assert r.status_code == 200

    # Old password no longer works, new one does.
    assert (
        await client.post(LOGIN, json={"email": "r@b.com", "password": "supersecret"})
    ).status_code == 401
    assert (
        await client.post(LOGIN, json={"email": "r@b.com", "password": "brandnewpass"})
    ).status_code == 200


async def test_password_reset_unknown_email_no_enumeration(client: AsyncClient) -> None:
    r = await client.post(RESET_REQUEST, json={"email": "ghost@b.com"})
    assert r.status_code == 202
    assert r.json()["reset_token"] is None


async def test_reset_token_rejected_as_access(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": "rt@b.com", "password": "supersecret"})
    token = (await client.post(RESET_REQUEST, json={"email": "rt@b.com"})).json()[
        "reset_token"
    ]
    # A reset token must not authenticate normal endpoints.
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 401
