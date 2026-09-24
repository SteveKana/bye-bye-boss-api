from __future__ import annotations

import json

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

_TWO_ITEMS = [
    *_ITEMS,
    BriefItem(
        match_id="22222222-2222-2222-2222-222222222222",
        title="Développeur Backend",
        company_name="Leclerc",
        url="https://example.com/opportunity/2",
        career_score=91,
    ),
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
    )

    assert ok is False


async def test_send_brief_whatsapp_sends_one_message_per_offer(monkeypatch) -> None:
    """The whole point of the per-offer template redesign: two matches must
    produce two distinct WhatsApp API calls, each carrying that one offer's
    own title/company/score/link -- never a single message summarizing
    both."""
    settings = get_settings()
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(settings, "WHATSAPP_TEMPLATE_NAME", "daily_brief")

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        ok = await send_brief_whatsapp(
            phone_number="+33612345678",
            first_name="Steve",
            items=_TWO_ITEMS,
            client=client,
        )
    finally:
        await client.aclose()

    assert ok is True
    assert len(requests) == 2
    bodies = [
        json.loads(r.content)["template"]["components"][0]["parameters"]
        for r in requests
    ]
    assert [p["text"] for p in bodies[0]] == [
        "Steve",
        "Product Owner Data",
        "Doctolib",
        "87",
        "https://example.com/opportunity/1",
    ]
    assert [p["text"] for p in bodies[1]] == [
        "Steve",
        "Développeur Backend",
        "Leclerc",
        "91",
        "https://example.com/opportunity/2",
    ]


async def test_send_brief_whatsapp_one_offer_failing_does_not_block_the_rest(
    monkeypatch,
) -> None:
    """A single rejected send (e.g. Meta throttling, a bad number) must not
    stop the rest of the brief -- the candidate should still get whichever
    offers *did* go through, and the overall result is still a success."""
    settings = get_settings()
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(settings, "WHATSAPP_TEMPLATE_NAME", "daily_brief")

    call_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(500)
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        ok = await send_brief_whatsapp(
            phone_number="+33612345678",
            first_name="Steve",
            items=_TWO_ITEMS,
            client=client,
        )
    finally:
        await client.aclose()

    assert ok is True  # the second offer still went out
    assert call_count == 2
