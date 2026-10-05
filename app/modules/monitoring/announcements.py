"""E-mail from the admin to a group of users.

Safeguards (Steve's validated design): a test to the admin's own address of
the *same* content within the last 24 hours is required before a real send;
the confirmed audience size must still match (a changed list needs a fresh
confirmation); every message carries an unsubscribe link and people who
unsubscribed are left out. The messages go through the normal mail outbox, so
they leave at the queue's pace, not all at once.
"""

from __future__ import annotations

import hashlib
import html
import re
import uuid
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.exceptions import BadRequestError, ConflictError
from app.modules.mailer import EmailStatus, MailerGateway, RenderedMail, render_mail
from app.modules.monitoring import queries as q
from app.modules.monitoring.common import as_utc, now_utc
from app.modules.monitoring.models import Announcement, AnnouncementOptOut
from app.modules.monitoring.tokens import unsubscribe_link

TEMPLATES = Path(__file__).parent / "templates"
TEST_VALIDITY = timedelta(hours=24)

AUDIENCE_LABELS = {
    "verified": "Tous les comptes confirmés",
    "unverified": "Comptes jamais confirmés",
    "no_cv": "Comptes sans CV importé",
    "never_applied": "Comptes n'ayant jamais candidaté",
    "all": "Tous les comptes",
}


def content_hash(subject: str, body: str) -> str:
    return hashlib.sha256(f"{subject}\n{body}".encode()).hexdigest()


def _personalize(text: str, first_name: str | None) -> str:
    name = (first_name or "").strip()
    if name:
        return re.sub(r"\{\{\s*pr[ée]nom\s*\}\}", name, text)
    text = re.sub(r"\s*\{\{\s*pr[ée]nom\s*\}\}", "", text)
    return text


def render_announcement(
    subject: str, body: str, *, first_name: str | None, user_id: uuid.UUID
) -> RenderedMail:
    personalized = _personalize(body, first_name)
    paragraphs = [p for p in re.split(r"\n\s*\n", personalized.strip()) if p.strip()]
    html_body = "".join(
        f'<p style="margin:0 0 14px">{html.escape(p).replace(chr(10), "<br>")}</p>'
        for p in paragraphs
    )
    return render_mail(
        TEMPLATES,
        "announcement",
        "fr",
        subject_line=_personalize(subject, first_name),
        body_text=personalized.strip(),
        body_html=html_body,
        unsubscribe_url=unsubscribe_link(user_id),
    )


async def opted_out_ids(session: AsyncSession) -> set[uuid.UUID]:
    rows = (await session.exec(select(AnnouncementOptOut.user_id))).all()
    return set(rows)


async def audience_members(session: AsyncSession, key: str) -> list:
    """Every active user of the audience (opted-out ones included)."""
    if key not in AUDIENCE_LABELS:
        raise BadRequestError("Audience inconnue.", code="unknown_audience")
    users = (
        await session.execute(
            sa.select(
                q.users.c.id,
                q.users.c.email,
                q.users.c.first_name,
                q.users.c.is_verified,
            ).where(q.users.c.is_active.is_(True))
        )
    ).all()
    if key == "all":
        return list(users)
    if key == "unverified":
        return [u for u in users if not u.is_verified]
    verified = [u for u in users if u.is_verified]
    if key == "verified":
        return verified
    profiles = (
        await session.execute(sa.select(q.profiles.c.id, q.profiles.c.user_id))
    ).all()
    profile_of_user = {p.user_id: p.id for p in profiles}
    if key == "no_cv":
        return [u for u in verified if u.id not in profile_of_user]
    # never_applied: has a profile, no application on any match.
    applied_profiles = {
        row[0]
        for row in (
            await session.execute(
                sa.select(q.matches.c.candidate_profile_id)
                .where(q.matches.c.application_status != "not_applied")
                .distinct()
            )
        ).all()
    }
    return [
        u
        for u in verified
        if u.id in profile_of_user and profile_of_user[u.id] not in applied_profiles
    ]


async def audience_counts(session: AsyncSession) -> list[tuple[str, str, int]]:
    out_ids = await opted_out_ids(session)
    result = []
    for key, label in AUDIENCE_LABELS.items():
        members = await audience_members(session, key)
        result.append((key, label, sum(1 for u in members if u.id not in out_ids)))
    return result


async def send_test(
    session: AsyncSession,
    *,
    admin_id: uuid.UUID,
    admin_email: str,
    subject: str,
    body: str,
) -> None:
    mail = render_announcement(subject, body, first_name="Prénom", user_id=admin_id)
    await MailerGateway(session).enqueue(
        to_email=admin_email,
        subject=f"[TEST] {mail.subject}",
        text=mail.text,
        html=mail.html,
    )
    session.add(
        Announcement(
            admin_id=admin_id,
            subject=subject,
            body=body,
            content_hash=content_hash(subject, body),
            audience="test",
            is_test=True,
            sent=1,
        )
    )
    await session.commit()


async def send_announcement(
    session: AsyncSession,
    *,
    admin_id: uuid.UUID,
    subject: str,
    body: str,
    audience: str,
    expected_count: int,
) -> Announcement:
    digest = content_hash(subject, body)
    tested = (
        await session.exec(
            select(Announcement).where(
                Announcement.admin_id == admin_id,
                Announcement.is_test.is_(True),  # type: ignore[attr-defined]
                Announcement.content_hash == digest,
                Announcement.created_at >= now_utc() - TEST_VALIDITY,
            )
        )
    ).first()
    if tested is None:
        raise BadRequestError(
            "Envoie-toi d'abord un test de ce message (« M'envoyer un test »).",
            code="test_required",
        )
    out_ids = await opted_out_ids(session)
    members = await audience_members(session, audience)
    recipients = [u for u in members if u.id not in out_ids]
    if len(recipients) != expected_count:
        raise ConflictError(
            f"La liste a changé : {len(recipients)} destinataire(s) au lieu de "
            f"{expected_count}. Vérifie puis confirme à nouveau.",
            code="audience_changed",
        )
    mailer = MailerGateway(session)
    for user in recipients:
        mail = render_announcement(
            subject, body, first_name=user.first_name, user_id=user.id
        )
        await mailer.enqueue(
            to_email=user.email, subject=mail.subject, text=mail.text, html=mail.html
        )
    announcement = Announcement(
        admin_id=admin_id,
        subject=subject,
        body=body,
        content_hash=digest,
        audience=audience,
        sent=len(recipients),
        skipped_unsubscribed=len(members) - len(recipients),
    )
    session.add(announcement)
    await session.commit()
    return announcement


async def failed_count(session: AsyncSession, announcement: Announcement) -> int:
    rows = (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(q.emails)
            .where(
                q.emails.c.subject == announcement.subject,
                q.emails.c.status == EmailStatus.failed.value,
                q.emails.c.created_at >= announcement.created_at,
            )
        )
    ).scalar_one()
    return int(rows)


async def history(session: AsyncSession) -> list[Announcement]:
    rows = (
        await session.exec(
            select(Announcement)
            .where(Announcement.is_test.is_(False))  # type: ignore[attr-defined]
            .order_by(Announcement.created_at.desc())  # type: ignore[attr-defined]
            .limit(50)
        )
    ).all()
    return list(rows)


def history_created(announcement: Announcement):
    return as_utc(announcement.created_at)
