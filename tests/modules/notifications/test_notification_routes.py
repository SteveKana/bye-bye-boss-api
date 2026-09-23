from __future__ import annotations

from httpx import AsyncClient

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
