from __future__ import annotations

from httpx import AsyncClient

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"


async def test_register_and_login_and_me(client: AsyncClient) -> None:
    payload = {"email": "a@b.com", "password": "supersecret", "full_name": "A"}
    r = await client.post(REGISTER, json=payload)
    assert r.status_code == 201
    assert r.json()["email"] == "a@b.com"
    assert r.json()["is_admin"] is False

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
