from __future__ import annotations

from httpx import AsyncClient
from sqlmodel import select

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.modules.mailer.models import EmailMessage, EmailStatus

CONTACT = "/api/v1/contact"

PAYLOAD = {
    "name": "Camille Martin",
    "email": "camille@example.com",
    "topic": "personal_data",
    "message": "Bonjour, je souhaite accéder à mes données personnelles.",
    "locale": "fr",
}


async def _queued() -> list[EmailMessage]:
    async with AsyncSessionLocal() as session:
        return list((await session.exec(select(EmailMessage))).all())


async def test_message_is_queued_for_the_site_owner(client: AsyncClient) -> None:
    r = await client.post(CONTACT, json=PAYLOAD)
    assert r.status_code == 201
    assert r.json()["detail"]

    queued = await _queued()
    assert len(queued) == 1
    mail = queued[0]
    assert mail.to_email == get_settings().CONTACT_RECIPIENT_EMAIL
    assert mail.status == EmailStatus.pending.value
    assert "Camille Martin" in mail.subject
    assert "Données personnelles" in mail.subject
    assert "camille@example.com" in mail.body_text
    assert "accéder à mes données" in mail.body_text
    assert mail.body_html is not None
    assert "mailto:camille@example.com" in mail.body_html


async def test_english_visitor_gets_english_confirmation(client: AsyncClient) -> None:
    r = await client.post(CONTACT, json={**PAYLOAD, "locale": "en"})
    assert r.status_code == 201
    assert "sent" in r.json()["detail"]


async def test_honeypot_answers_ok_but_sends_nothing(client: AsyncClient) -> None:
    r = await client.post(CONTACT, json={**PAYLOAD, "website": "http://spam.example"})
    assert r.status_code == 201
    assert await _queued() == []


async def test_line_breaks_in_the_name_never_reach_the_subject(
    client: AsyncClient,
) -> None:
    r = await client.post(
        CONTACT, json={**PAYLOAD, "name": "Eve\r\nBcc: victim@example.com"}
    )
    assert r.status_code == 201
    subject = (await _queued())[0].subject
    assert "\r" not in subject and "\n" not in subject


async def test_html_in_the_message_is_escaped_in_the_html_part(
    client: AsyncClient,
) -> None:
    r = await client.post(
        CONTACT, json={**PAYLOAD, "message": "<script>alert(1)</script> bonjour"}
    )
    assert r.status_code == 201
    mail = (await _queued())[0]
    assert "<script>" not in (mail.body_html or "")
    assert "&lt;script&gt;" in (mail.body_html or "")


async def test_invalid_payloads_are_rejected(client: AsyncClient) -> None:
    assert (
        await client.post(CONTACT, json={**PAYLOAD, "email": "nope"})
    ).status_code == 422
    assert (
        await client.post(CONTACT, json={**PAYLOAD, "message": "court"})
    ).status_code == 422
    assert (
        await client.post(CONTACT, json={**PAYLOAD, "name": "   "})
    ).status_code == 422
    assert (
        await client.post(CONTACT, json={**PAYLOAD, "topic": "unknown"})
    ).status_code == 422
    assert await _queued() == []
