"""Pluggable outgoing-mail transports.

`MAIL_PROVIDER` selects the active one. When it is not configured the message
is only logged instead of being sent, so capture -> queue -> send stays
exercisable without credentials (and nothing is silently lost: the queue still
marks the message as handled).
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.mailer.transports import mailgun, smtp

logger = get_logger("mailer.transport")

TRANSPORTS = {smtp.NAME: smtp, mailgun.NAME: mailgun}


def active_provider() -> str:
    return get_settings().MAIL_PROVIDER


def is_configured() -> bool:
    return TRANSPORTS[active_provider()].is_configured()


async def send_email(
    to_email: str, subject: str, text: str, html: str | None = None
) -> None:
    """Deliver one message with the active provider. Raises so the queue retries."""
    provider = active_provider()
    transport = TRANSPORTS[provider]

    if not transport.is_configured():
        logger.info(
            "email_not_sent_provider_unconfigured",
            provider=provider,
            to=to_email,
            subject=subject,
            preview=text[:120],
        )
        return

    await transport.send(to_email=to_email, subject=subject, text=text, html=html)
    logger.info("email_sent", provider=provider, to=to_email, subject=subject)
