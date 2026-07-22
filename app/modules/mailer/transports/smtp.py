"""SMTP transport (aiosmtplib). Works with MailHog locally, OVH in production."""

from __future__ import annotations

from email.message import EmailMessage as MimeMessage

import aiosmtplib

from app.core.config import get_settings

NAME = "smtp"


def is_configured() -> bool:
    return bool(get_settings().SMTP_HOST)


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


async def send(
    *, to_email: str, subject: str, text: str, html: str | None = None
) -> None:
    settings = get_settings()
    await aiosmtplib.send(
        _build_mime(to_email, subject, text, html),
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER or None,
        password=settings.SMTP_PASSWORD or None,
        start_tls=settings.SMTP_STARTTLS,
        timeout=settings.SMTP_TIMEOUT_SECONDS,
    )
