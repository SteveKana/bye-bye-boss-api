"""Scheduled matching: scores complete profiles against relevant offers once
a day, so a candidate's dashboard always reads a pre-computed result instead
of waiting on an LLM call.

Was an interval (every MATCHING_INTERVAL_MINUTES) until the 2026-09-22 cost
review -- running once daily at a fixed local time, right after the day's
new offers have had a chance to come in, cuts LLM spend further without
losing much freshness for a still-small candidate base (see the config
history in app/core/config.py for the earlier interval-based step).
"""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.matching.service import MatchingService

logger = get_logger("matching.worker")


@scheduled(
    cron="0 18 * * *",  # 18:00, every day
    timezone="Europe/Paris",
    id="matching_sync",
)
async def sync_matches() -> None:
    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).sync_all()
    if report.pairs_failed:
        logger.warning("matching_sync_had_failures", failed=report.pairs_failed)
