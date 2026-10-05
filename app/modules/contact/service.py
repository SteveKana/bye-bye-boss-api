from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.contact.schemas import ContactCreate
from app.modules.mailer import MailerGateway, render_mail

logger = get_logger("contact")

TEMPLATES = Path(__file__).parent / "templates"

TOPIC_LABELS = {
    "question": "Question",
    "personal_data": "Données personnelles (RGPD)",
    "problem": "Problème technique",
    "partnership": "Partenariat",
    "other": "Autre",
}

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]+")


def _one_line(value: str, limit: int = 80) -> str:
    """Collapse to a single clean line (the visitor's name ends up in the
    subject header, so no line break may ever get through)."""
    return _CONTROL_CHARS.sub(" ", value).strip()[:limit]


class ContactService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def send(self, data: ContactCreate) -> bool:
        """Queue the message for the site owner. Returns False when the
        honeypot was filled in (a bot): nothing is sent, the caller still
        answers with the normal success message."""
        if data.website:
            logger.info("contact_honeypot_triggered")
            return False

        name = _one_line(data.name)
        email = str(data.email).strip()
        mail = render_mail(
            TEMPLATES,
            "contact_message",
            "fr",
            sender_name=name,
            email=email,
            topic=TOPIC_LABELS[data.topic],
            message=data.message,
            visitor_locale=data.locale,
        )
        await MailerGateway(self.session).enqueue(
            to_email=get_settings().CONTACT_RECIPIENT_EMAIL,
            subject=mail.subject,
            text=mail.text,
            html=mail.html,
        )
        await self.session.commit()
        logger.info("contact_message_queued", topic=data.topic)
        return True
