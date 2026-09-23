"""Scheduled daily brief: one run per day, right after the matching job
(app/modules/matching/jobs.py, 18:00 Paris) has had a chance to refresh
every candidate's matches, so the brief reads that day's scores rather than
yesterday's.
"""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.notifications.service import DailyBriefService

logger = get_logger("notifications.worker")


@scheduled(
    cron="30 18 * * *",  # 18:30, every day -- 30 minutes after matching_sync
    timezone="Europe/Paris",
    id="notifications_daily_brief",
)
async def send_daily_briefs() -> None:
    async with AsyncSessionLocal() as session:
        report = await DailyBriefService(session).send_daily_briefs()
    if report.channel_failures:
        logger.warning(
            "notifications_daily_brief_had_failures", **report.channel_failures
        )
