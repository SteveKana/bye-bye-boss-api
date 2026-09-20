"""Scheduled matching: scores complete profiles against relevant offers on a
fixed interval, so a candidate's dashboard always reads a pre-computed
result instead of waiting on an LLM call."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.matching.service import MatchingService

logger = get_logger("matching.worker")


@scheduled(
    interval_minutes=get_settings().MATCHING_INTERVAL_MINUTES,
    id="matching_sync",
)
async def sync_matches() -> None:
    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).sync_all()
    if report.pairs_failed:
        logger.warning("matching_sync_had_failures", failed=report.pairs_failed)
