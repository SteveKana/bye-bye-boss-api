"""Email channel -- the one that's always available, no external account
needed (it reuses the `mailer` module's existing queue/transport). Wording
lives in ../templates/mail/daily_brief/, same convention as
app/modules/auth/emails.py.

No per-user locale to read (see NotificationPreference's docstring --
nothing in the app tracks one yet), so this always renders in French,
same default `render_mail` itself falls back to.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.mailer import MailerGateway, render_mail
from app.modules.notifications.brief_item import BriefItem

TEMPLATES = Path(__file__).parent.parent / "templates"


async def send_brief_email(
    session: AsyncSession,
    *,
    to_email: str,
    first_name: str | None,
    items: list[BriefItem],
) -> None:
    mail = render_mail(
        TEMPLATES,
        "daily_brief",
        "fr",
        first_name=first_name,
        items=items,
        count=len(items),
    )
    await MailerGateway(session).enqueue(
        to_email=to_email, subject=mail.subject, text=mail.text, html=mail.html
    )
