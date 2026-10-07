"""Scheduled ingestion: refreshes job offers on a fixed interval."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


@scheduled(
    interval_minutes=get_settings().OFFERS_INGESTION_INTERVAL_MINUTES,
    id="offers_sync_full",
)
async def sync_offers_full() -> None:
    """Keeps up with the whole France Travail catalogue: every offer created
    in the last few hours (overlapping runs are harmless, imports are
    idempotent). Off until OFFERS_FULL_SYNC_ENABLED is set."""
    settings = get_settings()
    if not settings.OFFERS_FULL_SYNC_ENABLED:
        return
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        report = await OffersIngestionService(session).sync_france_travail_full(
            since=now - timedelta(hours=settings.OFFERS_FULL_SYNC_WINDOW_HOURS),
            until=now,
        )
    logger.info(
        "offers_full_sync_done",
        fetched=report.fetched,
        created=report.created,
        updated=report.updated,
    )
