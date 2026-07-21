from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import utcnow
from app.modules.mailer.models import EmailMessage, EmailStatus
from app.modules.mailer.repository import EmailRepository
from app.modules.mailer.transports import send_email

logger = get_logger("mailer")


class MailerService:
    """Owns the outbox. `enqueue` never commits: the caller writes the mail in
    the same transaction as its business data (transactional outbox), so a mail
    is queued if and only if the data it refers to was persisted."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.emails = EmailRepository(session)

    async def enqueue(
        self, *, to_email: str, subject: str, text: str, html: str | None = None
    ) -> EmailMessage:
        message = await self.emails.create(
            EmailMessage(
                to_email=to_email,
                subject=subject,
                body_text=text,
                body_html=html,
            )
        )
        logger.info("email_queued", to=to_email, subject=subject)
        return message

    async def process_pending(self) -> int:
        """Flush a batch of the queue. Returns how many were sent."""
        settings = get_settings()
        pending = await self.emails.list_pending(settings.MAIL_QUEUE_BATCH_SIZE)
        sent = 0

        for message in pending:
            message.attempts += 1
            try:
                await send_email(
                    message.to_email,
                    message.subject,
                    message.body_text,
                    message.body_html,
                )
            except Exception as exc:
                message.last_error = str(exc)[:500]
                if message.attempts >= settings.MAIL_MAX_ATTEMPTS:
                    message.status = EmailStatus.failed.value
                    logger.error(
                        "email_gave_up", to=message.to_email, attempts=message.attempts
                    )
                else:
                    logger.warning(
                        "email_send_retry",
                        to=message.to_email,
                        attempts=message.attempts,
                        error=str(exc),
                    )
            else:
                message.status = EmailStatus.sent.value
                message.sent_at = utcnow()
                message.last_error = None
                sent += 1
            self.session.add(message)

        await self.session.commit()
        return sent
