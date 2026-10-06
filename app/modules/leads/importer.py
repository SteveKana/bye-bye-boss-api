"""One-off migration of waitlist leads into real accounts.

Safe by construction:
- a lead whose address already has an account is skipped;
- an account is created with e-mail only (no password, not verified);
- the launch invitation is queued at most once per lead (`invited_at`);
- nothing is written unless the caller asks (`create` / `send`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.auth import AuthGateway
from app.modules.leads.emails import build_invitation_email
from app.modules.leads.models import Lead
from app.modules.leads.repository import LeadRepository
from app.modules.mailer import MailerGateway

logger = get_logger("leads")


@dataclass
class ImportReport:
    leads_total: int = 0
    already_accounts: list[str] = field(default_factory=list)
    to_import: list[str] = field(default_factory=list)
    accounts_created: list[str] = field(default_factory=list)
    invitations_queued: list[str] = field(default_factory=list)
    already_invited: list[str] = field(default_factory=list)


async def import_leads(
    session: AsyncSession, *, apply: bool = False, limit: int | None = None
) -> ImportReport:
    """Creates the missing accounts and queues one invitation each when
    `apply` is True; otherwise a dry run that only reports what would happen."""
    report = ImportReport()
    auth = AuthGateway(session)
    mailer = MailerGateway(session)
    leads = await LeadRepository(session).list(order_by=Lead.created_at)
    report.leads_total = len(leads)
    days = get_settings().INVITATION_TOKEN_TTL_MINUTES // (60 * 24)

    for lead in leads:
        email = lead.email.strip().lower()
        if lead.invited_at is not None:
            report.already_invited.append(email)
            continue
        if await auth.email_has_account(email):
            # Signed up on its own: nothing to import, nothing to send.
            report.already_accounts.append(email)
            continue
        if limit is not None and len(report.to_import) >= limit:
            break
        report.to_import.append(email)
        if not apply:
            continue

        user = await auth.create_invited_account(email)
        if user is None:  # raced with a normal signup: leave it alone
            report.to_import.remove(email)
            report.already_accounts.append(email)
            continue
        report.accounts_created.append(email)

        link = await auth.invitation_link(user.id)
        if link is None:
            continue
        mail = build_invitation_email(lead.locale, link, days)
        await mailer.enqueue(
            to_email=email, subject=mail.subject, text=mail.text, html=mail.html
        )
        lead.invited_at = datetime.now(UTC)
        session.add(lead)
        await session.commit()
        report.invitations_queued.append(email)
        logger.info("lead_invitation_queued", email=email)

    return report
