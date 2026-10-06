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
from app.modules.notifications.links import notification_settings_url

_ITEMS = [
    BriefItem(
        match_id="11111111-1111-1111-1111-111111111111",
        title="Product Owner Data",
        company_name="Doctolib",
        url="https://example.com/opportunity/1",
        career_score=87,
        # Deliberately different from career_score in these fixtures, so a
        # test asserting on this value can't accidentally pass because the
        # two numbers happen to match.
        ats_potential=72,
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
        ats_potential=64,
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
    # 4th value is ats_potential (72/64), not career_score (87/91) -- the
    # WhatsApp template's "match_score" placeholder deliberately carries
    # ats_potential, see whatsapp_channel.py's module docstring.
    assert [p["text"] for p in bodies[0]] == [
        "Steve",
        "Product Owner Data",
        "Doctolib",
        "72",
        "https://example.com/opportunity/1",
    ]
    assert [p["text"] for p in bodies[1]] == [
        "Steve",
        "Développeur Backend",
        "Leclerc",
        "64",
        "https://example.com/opportunity/2",
    ]
    # The template now uses POSITIONAL variables ({{1}}..{{5}}), not named
    # ones -- a "parameter_name" key here would make Meta reject the send
    # with "(#100) Invalid parameter" (see whatsapp_channel.py's module
    # docstring). Locks in the NAMED->POSITIONAL switch as a regression.
    for body in bodies:
        for parameter in body:
            assert "parameter_name" not in parameter


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


async def test_discord_brief_carries_the_notification_settings_link() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        await send_brief_discord(
            webhook_url="https://discord.com/api/webhooks/1/abc",
            items=_ITEMS,
            client=client,
        )
    finally:
        await client.aclose()

    fields = captured["body"]["embeds"][0]["fields"]
    assert len(fields) == len(_ITEMS) + 1
    assert fields[-1]["value"] == (
        f"[Désactiver ces notifications]({notification_settings_url()})"
    )
    assert notification_settings_url().endswith("/settings#notifications")


async def test_whatsapp_sends_settings_link_only_with_the_new_template(
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "WHATSAPP_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "WHATSAPP_PHONE_NUMBER_ID", "123456")
    monkeypatch.setattr(settings, "WHATSAPP_TEMPLATE_NAME", "daily_brief_v2")
    sent: list[list[str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        params = json.loads(request.content)["template"]["components"][0]["parameters"]
        sent.append([p["text"] for p in params])
        return httpx.Response(200, json={"messages": [{"id": "wamid.test"}]})

    for has_link in (False, True):
        monkeypatch.setattr(settings, "WHATSAPP_TEMPLATE_HAS_SETTINGS_LINK", has_link)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await send_brief_whatsapp(
                phone_number="+33612345678",
                first_name="Steve",
                items=_ITEMS[:1],
                client=client,
            )
        finally:
            await client.aclose()

    assert len(sent[0]) == 5  # original template: unchanged payload
    assert len(sent[1]) == 6
    assert sent[1][5] == notification_settings_url()


async def test_brief_email_has_the_notification_settings_link(
    client, auth_headers
) -> None:
    from app.core.database import AsyncSessionLocal
    from app.modules.mailer.models import EmailMessage  # noqa: PLC0415
    from app.modules.notifications.channels.email_channel import send_brief_email

    async with AsyncSessionLocal() as session:
        await send_brief_email(
            session, to_email="a@b.fr", first_name="Steve", items=_ITEMS
        )
        await session.commit()
        from sqlmodel import select  # noqa: PLC0415

        mail = (await session.exec(select(EmailMessage))).all()[-1]

    url = notification_settings_url()
    assert url in mail.body_text
    assert f'href="{url}"' in mail.body_html
