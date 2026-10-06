"""Housekeeping: page views are only kept for MONITORING_PAGE_VIEW_RETENTION_DAYS."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import col

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.monitoring.common import now_utc
from app.modules.monitoring.models import PageView

logger = get_logger("monitoring.jobs")


@scheduled(cron="30 3 * * *", id="monitoring_purge_page_views")
async def purge_old_page_views() -> None:
    cutoff = now_utc() - timedelta(
        days=get_settings().MONITORING_PAGE_VIEW_RETENTION_DAYS
    )
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            delete(PageView).where(col(PageView.created_at) < cutoff)
        )
        await session.commit()
    logger.info("page_views_purged", rows=getattr(result, "rowcount", 0))
