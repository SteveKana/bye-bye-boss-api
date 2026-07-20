"""Synchronous cross-module access point for queuing mail.

Other modules depend on `MailerGateway` instead of importing the outbox model
or the transport. Queuing takes part in the caller's transaction — commit is
the caller's responsibility.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.mailer.service import MailerService


class MailerGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.mailer = MailerService(session)

    async def enqueue(
        self, *, to_email: str, subject: str, text: str, html: str | None = None
    ) -> None:
        await self.mailer.enqueue(
            to_email=to_email, subject=subject, text=text, html=html
        )
