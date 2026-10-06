"""Notifications-owned event listeners."""

from __future__ import annotations

from sqlmodel import col, delete

from app.core.database import AsyncSessionLocal
from app.core.events import on
from app.core.logging import get_logger
from app.modules.auth import UserDeletionRequested, UserRegistered
from app.modules.matching import MatchesScored
from app.modules.notifications.models import (
    NotificationBriefEntry,
    NotificationPreference,
)
from app.modules.notifications.service import (
    DailyBriefService,
    NotificationPreferenceService,
)

logger = get_logger("notifications.listeners")


@on(UserRegistered)
async def create_default_preferences_on_signup(event: UserRegistered) -> None:
    """Email notifications are on by default from the moment the account is
    created (Steve, 2026-10-04). Until then the preference row only
    appeared the first time the candidate opened the notification settings
    screen, so anyone who never did got no brief at all."""
    async with AsyncSessionLocal() as session:
        await NotificationPreferenceService(session).get_or_create(event.user_id)
        await session.commit()


@on(MatchesScored)
async def send_brief_when_matches_scored(event: MatchesScored) -> None:
    """The daily brief goes out as soon as a candidate's new matches have
    been analysed (the Batch API has no fixed completion time, so the former
    fixed 18:30 schedule no longer fits -- see matching/events.py)."""
    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_briefs_for_profiles(
            event.profile_ids
        )
    if report.channel_failures:
        logger.warning("notifications_brief_had_failures", **report.channel_failures)


@on(UserDeletionRequested)
async def purge_notification_data(event: UserDeletionRequested) -> None:
    """Account deletion: the candidate's channel preferences and the log of
    briefs already sent to them."""
    async with AsyncSessionLocal() as session:
        await session.exec(
            delete(NotificationBriefEntry).where(
                col(NotificationBriefEntry.user_id) == event.user_id
            )
        )
        await session.exec(
            delete(NotificationPreference).where(
                col(NotificationPreference.user_id) == event.user_id
            )
        )
        await session.commit()
