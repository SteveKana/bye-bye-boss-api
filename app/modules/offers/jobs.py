"""Scheduled ingestion: refreshes job offers on a fixed interval."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.offers.service import OffersIngestionService

logger = get_logger("offers.worker")


@scheduled(
    interval_minutes=get_settings().OFFERS_INGESTION_INTERVAL_MINUTES,
    id="offers_sync",
)
async def sync_offers() -> None:
    async with AsyncSessionLocal() as session:
        report = await OffersIngestionService(session).sync()
    if report.skipped_unconfigured:
        logger.warning(
            "offers_sync_skipped_providers", providers=report.skipped_unconfigured
        )
