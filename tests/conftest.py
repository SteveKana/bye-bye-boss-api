"""Test configuration.

Tests run against an isolated file-backed SQLite database with a fresh schema
per test — no external infra required. (File-backed, not `:memory:`, so the
request session and any listener-opened sessions share the same data.)
"""

from __future__ import annotations

import os

# Must be set before importing the app (settings are cached on first import).
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_app.db"
os.environ["APP_ENV"] = "test"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DATABASE_AUTO_CREATE"] = "false"
# Off by default in tests; the rate-limit test opts back in explicitly.
os.environ["RATE_LIMIT_ENABLED"] = "false"
# Never let a developer's .env (MailHog, Mailgun keys...) reach the suite:
# with no provider configured the transport only logs, so tests stay hermetic.
os.environ["MAIL_PROVIDER"] = "smtp"
os.environ["SMTP_HOST"] = ""
os.environ["MAILGUN_SECRET"] = ""
os.environ["MAILGUN_DOMAIN"] = ""
os.environ.setdefault("SECRET_KEY", "test-secret-key-at-least-32-characters-long")
os.environ.pop("ADMIN_EMAIL", None)
os.environ.pop("ADMIN_PASSWORD", None)

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlmodel import SQLModel, select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.auth.models import User  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def _reset_schema():
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)
        await conn.run_sync(SQLModel.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
def verify_user():
    """Mark an account as email-verified (email delivery is out of band in tests,
    and login is blocked until verification)."""

    async def _verify(email: str) -> None:
        async with AsyncSessionLocal() as session:
            user = (await session.exec(select(User).where(User.email == email))).first()
            user.is_verified = True
            session.add(user)
            await session.commit()

    return _verify


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, verify_user) -> dict[str, str]:
    """Register + verify + login a user, returning ready-to-use headers."""
    payload = {
        "email": "user@example.com",
        "password": "supersecret",
        "first_name": "U",
    }
    await client.post("/api/v1/auth/register", json=payload)
    await verify_user(payload["email"])
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": payload["email"], "password": payload["password"]},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
