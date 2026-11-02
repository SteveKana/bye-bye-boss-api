"""Scheduled market-watch collection.

  * `collect_market_data`, every 30 minutes: files the offers that have no
    tags yet (the first runs after the feature ships catch up on every offer
    already in the database, MARKET_OFFERS_PER_RUN at a time), then refreshes
    the weekly counts when something new was filed;
  * `aggregate_market_weeks`, daily at 02:45 Paris time -- before the
    03:30 offer purge -- so a week's final counts are stored while all its
    offers still exist.

Both never raise: a problem here must not affect anything else, the next run
simply tries again.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.market.service import MarketCollectionService

logger = get_logger("market.jobs")


@scheduled(interval_minutes=30, id="market_collect")
async def collect_market_data() -> None:
    if not get_settings().MARKET_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as session:
            service = MarketCollectionService(session)
            report = await service.classify_pending()
            if report.classified:
                await service.aggregate_weeks()
    except Exception:
        logger.exception("market_collect_failed")
        return
    if report.classified or report.failed:
        logger.info(
            "market_offers_classified",
            classified=report.classified,
            failed=report.failed,
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
        )


@scheduled(
    cron="45 2 * * *",
    timezone="Europe/Paris",
    id="market_aggregate",
)
async def aggregate_market_weeks() -> None:
    if not get_settings().MARKET_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as session:
            groups = await MarketCollectionService(session).aggregate_weeks()
    except Exception:
        logger.exception("market_aggregate_failed")
        return
    logger.info("market_weeks_aggregated", groups=groups)
