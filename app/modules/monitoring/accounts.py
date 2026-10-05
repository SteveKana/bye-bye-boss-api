"""Admin actions on a single account (buttons of "Comptes à relancer")."""

from __future__ import annotations

import uuid
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.modules.auth import AuthGateway
from app.modules.mailer import MailerGateway, render_mail
from app.modules.monitoring import queries as q
from app.modules.monitoring.announcements import opted_out_ids
from app.modules.monitoring.tokens import unsubscribe_link

TEMPLATES = Path(__file__).parent / "templates"


async def run_action(session: AsyncSession, user_id: uuid.UUID, action: str) -> str:
    row = (
        await session.execute(
            sa.select(
                q.users.c.id,
                q.users.c.email,
                q.users.c.first_name,
                q.users.c.is_verified,
                q.users.c.is_active,
            ).where(q.users.c.id == user_id)
        )
    ).first()
    if row is None or not row.is_active:
        raise NotFoundError("Compte introuvable.")

    if action == "resend_verification":
        if not await AuthGateway(session).queue_verification_email(user_id):
            raise BadRequestError(
                "Ce compte a déjà confirmé son adresse.", code="already_verified"
            )
        return "Lien de confirmation renvoyé."

    if action != "reminder":
        raise BadRequestError("Action inconnue.", code="unknown_action")
    if user_id in await opted_out_ids(session):
        raise BadRequestError(
            "Cet utilisateur a demandé à ne plus recevoir de messages.",
            code="opted_out",
        )
    profile = (
        await session.execute(
            sa.select(q.profiles.c.id).where(q.profiles.c.user_id == user_id)
        )
    ).first()
    stage = "no_cv" if profile is None else "no_application"
    base = get_settings().APP_URL.rstrip("/")
    mail = render_mail(
        TEMPLATES,
        "account_reminder",
        "fr",
        stage=stage,
        first_name=row.first_name,
        link=f"{base}/onboarding/upload"
        if stage == "no_cv"
        else f"{base}/opportunites",
        unsubscribe_url=unsubscribe_link(user_id),
    )
    await MailerGateway(session).enqueue(
        to_email=row.email, subject=mail.subject, text=mail.text, html=mail.html
    )
    await session.commit()
    return "Rappel envoyé."
