from __future__ import annotations

import pytest_asyncio
from httpx import AsyncClient

from app.core import ratelimit
from app.core.config import get_settings

LOGIN = "/api/v1/auth/login"


@pytest_asyncio.fixture
async def rate_limiting_on():
    """Enable rate limiting for one test and reset the counter store around it."""
    settings = get_settings()
    settings.RATE_LIMIT_ENABLED = True
    await ratelimit.backend.reset()
    try:
        yield
    finally:
        settings.RATE_LIMIT_ENABLED = False
        await ratelimit.backend.reset()


async def test_login_is_rate_limited(client: AsyncClient, rate_limiting_on) -> None:
    # login_limit is 10 per 60s -> the 11th attempt within the window is blocked.
    statuses = []
    for _ in range(12):
        r = await client.post(LOGIN, json={"email": "x@x.com", "password": "nope"})
        statuses.append(r.status_code)

    assert statuses[0] == 401  # limiter lets valid traffic through (bad creds -> 401)
    assert 429 in statuses  # ...then trips once the budget is exhausted

    blocked = await client.post(LOGIN, json={"email": "x@x.com", "password": "nope"})
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in blocked.headers


async def test_rate_limit_headers_present(
    client: AsyncClient, rate_limiting_on
) -> None:
    # Headers ride on successful responses (2xx); a fresh login scope -> 9 left.
    await client.post(
        "/api/v1/auth/register",
        json={"email": "h@h.com", "password": "supersecret"},
    )
    r = await client.post(
        LOGIN, json={"email": "h@h.com", "password": "supersecret"}
    )
    assert r.status_code == 200
    assert r.headers["X-RateLimit-Limit"] == "10"
    assert r.headers["X-RateLimit-Remaining"] == "9"


async def test_disabled_by_default(client: AsyncClient) -> None:
    # With RATE_LIMIT_ENABLED false (default in tests), no throttling headers.
    r = await client.post(LOGIN, json={"email": "x@x.com", "password": "nope"})
    assert "X-RateLimit-Limit" not in r.headers
