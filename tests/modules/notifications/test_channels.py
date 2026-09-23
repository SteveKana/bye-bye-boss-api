from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.modules.notifications.brief_item import BriefItem
from app.modules.notifications.channels.discord_channel import send_brief_discord
from app.modules.notifications.channels.whatsapp_channel import (
    is_configured,
    send_brief_whatsapp,
)

_ITEMS = [
    BriefItem(
        match_id="11111111-1111-1111-1111-111111111111",
        title="Product Owner Data",
        company_name="Doctolib",
        url="https://example.com/opportunity/1",
        career_score=87,
    )
]


async def test_send_brief_discord_success() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.content
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        ok = await send_brief_discord(
            webhook_url="https://discord.com/api/webhooks/1/abc",
            items=_ITEMS,
            client=client,
        )
    finally:
        await client.aclose()

    assert ok is True
    assert captured["url"] == "https://discord.com/api/webhooks/1/abc"


async def test_send_brief_discord_http_error_returns_false() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        ok = await send_brief_discord(
            webhook_url="https://discord.com/api/webhooks/1/abc",
            items=_ITEMS,
            client=client,
        )
    finally:
        await client.aclose()

    assert ok is False


async def test_whatsapp_is_configured_false_by_default() -> None:
    # WHATSAPP_* is unset in the test environment (conftest.py sets none of
    # it) -- confirms the channel starts unconfigured, same as production
    # would before Steve creates the Meta Business API credentials.
    assert is_configured() is False


async def test_send_brief_whatsapp_noops_when_unconfigured() -> None:
    ok = await send_brief_whatsapp(
        phone_number="+33612345678",
        first_name="Steve",
        items=_ITEMS,
        dashboard_url="https://dev.byebyeboss.fr/dashboard",
    )

    assert ok is False


async def test_send_brief_whatsapp_sends_template_when_configured(
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(settings, "WHATSAPP_TEMPLATE_NAME", "daily_brief")

    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        ok = await send_brief_whatsapp(
            phone_number="+33612345678",
            first_name="Steve",
            items=_ITEMS,
            dashboard_url="https://dev.byebyeboss.fr/dashboard",
            client=client,
        )
    finally:
        await client.aclose()

    assert ok is True
    assert "graph.facebook.com" in captured["url"]
    assert captured["auth"] == "Bearer test-token"


async def test_send_brief_whatsapp_returns_false_with_no_items() -> None:
    ok = await send_brief_whatsapp(
        phone_number="+33612345678",
        first_name="Steve",
        items=[],
        dashboard_url="https://dev.byebyeboss.fr/dashboard",
    )

    assert ok is False
