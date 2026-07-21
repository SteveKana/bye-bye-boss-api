"""Mailgun transport (HTTP API).

A non-2xx response raises, so the queue worker retries like any other failure.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings

NAME = "mailgun"


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.MAILGUN_SECRET and settings.MAILGUN_DOMAIN)


async def send(
    *, to_email: str, subject: str, text: str, html: str | None = None
) -> None:
    settings = get_settings()
    url = f"{settings.mailgun_base_url}/v3/{settings.MAILGUN_DOMAIN}/messages"
    data = {
        "from": f"{settings.EMAIL_FROM_NAME} <{settings.EMAIL_FROM}>",
        "to": to_email,
        "subject": subject,
        "text": text,
    }
    if html:
        data["html"] = html

    async with httpx.AsyncClient(timeout=settings.MAILGUN_TIMEOUT_SECONDS) as client:
        response = await client.post(
            url, auth=("api", settings.MAILGUN_SECRET or ""), data=data
        )
    response.raise_for_status()
