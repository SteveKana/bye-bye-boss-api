from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import event_bus
from app.core.logging import get_logger
from app.modules.leads.emails import build_ack_email
from app.modules.leads.events import LeadCaptured
from app.modules.leads.models import Lead
from app.modules.leads.repository import LeadRepository
from app.modules.leads.schemas import LeadCreate
from app.modules.mailer import MailerGateway

logger = get_logger("leads")


class LeadService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.leads = LeadRepository(session)

    async def capture(self, data: LeadCreate) -> tuple[Lead, bool]:
        """Register a waitlist email. Returns (lead, created).

        Re-submitting a known address is a no-op: no duplicate row and no second
        acknowledgement. The lead and its acknowledgement mail are written in the
        same transaction, so a queued mail always refers to a persisted lead.
        """
        email = str(data.email).strip().lower()

        existing = await self.leads.get_by_email(email)
        if existing:
            logger.info("lead_already_registered", email=email)
            return existing, False

        lead = await self.leads.create(
            Lead(email=email, locale=data.locale, source=data.source)
        )

        ack = build_ack_email(data.locale)
        await MailerGateway(self.session).enqueue(
            to_email=email, subject=ack.subject, text=ack.text, html=ack.html
        )

        await self.session.commit()
        logger.info("lead_captured", lead_id=str(lead.id), email=email)

        await event_bus.emit(
            LeadCaptured(lead_id=lead.id, email=lead.email, locale=lead.locale)
        )
        return lead, True

    async def list_all(self) -> Sequence[Lead]:
        return await self.leads.list(order_by=Lead.created_at)

    async def count(self) -> int:
        return await self.leads.count()
