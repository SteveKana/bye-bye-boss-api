"""SMTP transport.

Without `SMTP_HOST` the message is only logged instead of being sent, so the
capture -> queue -> send flow is fully exercisable before credentials exist.
"""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage

import aiosmtplib

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("mailer.transport")


def _build_mime(
    to_email: str, subject: str, text: str, html: str | None
) -> MimeMessage:
    settings = get_settings()
    message = MimeMessage()
    message["From"] = f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>"
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    return message


async def send_email(
    to_email: str, subject: str, text: str, html: str | None = None
) -> None:
    """Deliver one message. Raises on failure so the queue can retry."""
    settings = get_settings()

    if not settings.SMTP_HOST:
        logger.info(
            "email_not_sent_no_smtp", to=to_email, subject=subject, preview=text[:120]
        )
        return

    await aiosmtplib.send(
        _build_mime(to_email, subject, text, html),
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=settings.SMTP_STARTTLS,
        timeout=settings.SMTP_TIMEOUT_SECONDS,
    )
    logger.info("email_sent", to=to_email, subject=subject)
