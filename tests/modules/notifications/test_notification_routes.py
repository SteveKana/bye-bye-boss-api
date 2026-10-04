from __future__ import annotations

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.notifications.repository import NotificationPreferenceRepository

_URL = "/api/v1/notifications/preferences"


async def test_get_preferences_requires_auth(client: AsyncClient) -> None:
    r = await client.get(_URL)
    assert r.status_code == 401


async def test_get_preferences_creates_defaults_on_first_read(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get(_URL, headers=auth_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["email_enabled"] is True
    assert body["discord_enabled"] is False
    assert body["discord_webhook_url"] is None
    assert body["whatsapp_enabled"] is False
    assert body["whatsapp_phone_number"] is None


async def test_get_preferences_is_stable_across_calls(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """The first GET creates the row -- a second GET must return the same
    one, not create a duplicate (the unique index on user_id would reject
    a second row, but a bug here could instead silently return a fresh
    default every time)."""
    await client.put(_URL, headers=auth_headers, json={"email_enabled": False})

    r = await client.get(_URL, headers=auth_headers)

    assert r.json()["email_enabled"] is False


async def test_update_preferences_partial_update_only_changes_sent_fields(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.put(_URL, headers=auth_headers, json={"email_enabled": False})

    assert r.status_code == 200
    body = r.json()
    assert body["email_enabled"] is False
    assert body["discord_enabled"] is False  # untouched, still the default


async def test_update_preferences_enabling_discord_without_webhook_fails(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.put(_URL, headers=auth_headers, json={"discord_enabled": True})

    assert r.status_code == 400


async def test_update_preferences_discord_webhook_must_look_like_discord(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.put(
        _URL,
        headers=auth_headers,
        json={
            "discord_enabled": True,
            "discord_webhook_url": "https://evil.example.com/steal",
        },
    )

    assert r.status_code == 400


async def test_update_preferences_enabling_discord_with_valid_webhook_succeeds(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    webhook = "https://discord.com/api/webhooks/123/abc"

    r = await client.put(
        _URL,
        headers=auth_headers,
        json={"discord_enabled": True, "discord_webhook_url": webhook},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["discord_enabled"] is True
    assert body["discord_webhook_url"] == webhook


async def test_update_preferences_enabling_whatsapp_without_phone_fails(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.put(_URL, headers=auth_headers, json={"whatsapp_enabled": True})

    assert r.status_code == 400


async def test_update_preferences_enabling_whatsapp_with_phone_succeeds(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.put(
        _URL,
        headers=auth_headers,
        json={"whatsapp_enabled": True, "whatsapp_phone_number": "+33612345678"},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["whatsapp_enabled"] is True
    assert body["whatsapp_phone_number"] == "+33612345678"


async def test_test_send_requires_auth(client: AsyncClient) -> None:
    r = await client.post(f"{_URL}/test-send")
    assert r.status_code == 401


async def test_test_send_fails_without_a_complete_profile(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # auth_headers registers a fresh user with no CV/profile at all yet --
    # nothing to build a brief from, regardless of channel configuration.
    r = await client.post(f"{_URL}/test-send", headers=auth_headers)
    assert r.status_code == 400


async def test_signup_creates_default_preferences_with_email_enabled(
    client: AsyncClient,
) -> None:
    """Email notifications are on from the moment the account exists (Steve,
    2026-10-04) -- no need to ever open the settings screen first."""
    await client.post(
        "/api/v1/auth/register",
        json={"email": "fresh@example.com", "password": "supersecret"},
    )

    async with AsyncSessionLocal() as session:
        user = (
            await session.exec(select(User).where(User.email == "fresh@example.com"))
        ).first()
        preference = await NotificationPreferenceRepository(session).get_by_user(
            user.id
        )

    assert preference is not None
    assert preference.email_enabled is True
    assert preference.discord_enabled is False
    assert preference.whatsapp_enabled is False
