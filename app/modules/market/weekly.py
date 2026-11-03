"""The Monday digest: one e-mail per eligible profile, once a week.

Eligible = complete profile, confirmed address, not unsubscribed from the
digest, specialty above the offer threshold, and fresh data (the digest must
cover last week). Everyone else is simply skipped -- never an empty e-mail.

Safe to run as often as wanted: a (person, week) already sent is skipped, so
the scheduler can retry through the day without ever sending twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.auth import AuthGateway
from app.modules.cv import CandidateProfile, CandidateProfileRepository, ProfileStatus
from app.modules.mailer import MailerGateway
from app.modules.market.models import MarketDigestOptOut, MarketDigestSend
from app.modules.market.render import render_digest
from app.modules.market.service import (
    DigestUnavailableError,
    MarketDigestService,
    today_paris,
)

logger = get_logger("market.weekly")


@dataclass
class WeeklyReport:
    sent: int = 0
    already_sent: int = 0
    opted_out: int = 0
    unverified: int = 0
    not_allowed: int = 0
    below_threshold: int = 0
    stale_data: int = 0
    failed: int = 0


def _allowed(email: str) -> bool:
    """False when a test-server allow-list is configured (same setting as
    the daily brief) and this address is not on it."""
    raw = get_settings().NOTIFICATIONS_ALLOWED_EMAILS
    allowed = {item.strip().lower() for item in raw.split(",") if item.strip()}
    return not allowed or email.strip().lower() in allowed


def expected_week(now: datetime | None = None) -> date:
    """Monday of the week that just ended -- the week a digest sent today
    must cover."""
    today = today_paris(now)
    return today - timedelta(days=today.weekday() + 7)


async def record_send(
    session: AsyncSession, user_id: uuid.UUID, week_start: date, source: str
) -> None:
    """Remembers a digest sent (idempotent: a second record is ignored)."""
    exists = (
        await session.execute(
            sa.select(MarketDigestSend.id).where(  # type: ignore[call-overload]
                MarketDigestSend.user_id == user_id,  # type: ignore[arg-type]
                MarketDigestSend.week_start == week_start,  # type: ignore[arg-type]
            )
        )
    ).first()
    if exists is None:
        session.add(
            MarketDigestSend(user_id=user_id, week_start=week_start, source=source)
        )


async def send_weekly_digests(
    session: AsyncSession, now: datetime | None = None
) -> WeeklyReport:
    report = WeeklyReport()
    wanted_week = expected_week(now)
    profiles = await CandidateProfileRepository(session).list(
        filters={"status": ProfileStatus.complete.value}
    )
    opted_out = {
        row[0]
        for row in (
            await session.execute(sa.select(MarketDigestOptOut.user_id))  # type: ignore[call-overload]
        ).all()
    }
    sent_this_week = {
        row[0]
        for row in (
            await session.execute(
                sa.select(MarketDigestSend.user_id).where(  # type: ignore[call-overload]
                    MarketDigestSend.week_start == wanted_week  # type: ignore[arg-type]
                )
            )
        ).all()
    }
    for profile in profiles:
        try:
            await _send_one(
                session, profile, wanted_week, opted_out, sent_this_week, report
            )
        except Exception:
            await session.rollback()
            report.failed += 1
            logger.exception("market_weekly_digest_failed", profile=str(profile.id))
    return report


async def _send_one(
    session: AsyncSession,
    profile: CandidateProfile,
    wanted_week: date,
    opted_out: set[uuid.UUID],
    sent_this_week: set[uuid.UUID],
    report: WeeklyReport,
) -> None:
    user_id = profile.user_id
    if user_id in sent_this_week:
        report.already_sent += 1
        return
    if user_id in opted_out:
        report.opted_out += 1
        return
    user = await AuthGateway(session).get_user(user_id)
    if user is None or not user.is_verified:
        report.unverified += 1
        return
    if not _allowed(str(user.email)):
        report.not_allowed += 1
        return

    try:
        result = await MarketDigestService(session).build(profile)
    except DigestUnavailableError as exc:
        # Below the threshold, nothing collected yet, or the profile's
        # specialty could not be determined: no e-mail this week.
        logger.info("market_weekly_digest_skipped", reason=str(exc)[:200])
        report.below_threshold += 1
        return
    if result.data.week_start < wanted_week:
        report.stale_data += 1
        return

    mail = render_digest(result.data, user_id)
    await MailerGateway(session).enqueue(
        to_email=str(user.email), subject=mail.subject, text=mail.text, html=mail.html
    )
    await record_send(session, user_id, result.data.week_start, "auto")
    await session.commit()
    sent_this_week.add(user_id)
    report.sent += 1
