from __future__ import annotations

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.leads.models import Lead
from app.modules.mailer.models import EmailMessage, EmailStatus
from app.modules.mailer.service import MailerService

LEADS = "/api/v1/leads"


async def _rows(model):
    async with AsyncSessionLocal() as session:
        return list((await session.exec(select(model))).all())


async def test_capture_stores_lead_and_queues_ack(client: AsyncClient) -> None:
    r = await client.post(LEADS, json={"email": "Lead@Example.com", "locale": "fr"})
    assert r.status_code == 201
    assert r.json()["detail"]

    leads = await _rows(Lead)
    assert len(leads) == 1
    assert leads[0].email == "lead@example.com"  # normalised

    queued = await _rows(EmailMessage)
    assert len(queued) == 1
    assert queued[0].to_email == "lead@example.com"
    assert queued[0].status == EmailStatus.pending.value
    assert "liste d'attente" in queued[0].subject


async def test_capture_is_idempotent(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "locale": "fr"}
    assert (await client.post(LEADS, json=payload)).status_code == 201
    # Same address again: same friendly response, no duplicate, no second mail.
    assert (await client.post(LEADS, json=payload)).status_code == 201

    assert len(await _rows(Lead)) == 1
    assert len(await _rows(EmailMessage)) == 1


async def test_locale_drives_the_acknowledgement(client: AsyncClient) -> None:
    await client.post(LEADS, json={"email": "en@example.com", "locale": "en"})
    queued = await _rows(EmailMessage)
    assert "waitlist" in queued[0].subject.lower()


async def test_invalid_email_is_rejected(client: AsyncClient) -> None:
    r = await client.post(LEADS, json={"email": "not-an-email"})
    assert r.status_code == 422


async def test_queue_worker_marks_messages_sent(client: AsyncClient) -> None:
    await client.post(LEADS, json={"email": "queue@example.com", "locale": "fr"})

    # No SMTP configured in tests: the transport logs instead of sending, so the
    # message still completes its lifecycle.
    async with AsyncSessionLocal() as session:
        sent = await MailerService(session).process_pending()
    assert sent == 1

    queued = await _rows(EmailMessage)
    assert queued[0].status == EmailStatus.sent.value
    assert queued[0].sent_at is not None
    assert queued[0].attempts == 1


async def test_listing_leads_requires_admin(client: AsyncClient) -> None:
    assert (await client.get(LEADS)).status_code == 401
