"""Retention: drops offers older than OFFERS_RETENTION_DAYS.

Lives in `matching` (not `offers`) because it must spare every offer a
candidate already has a match on -- that table belongs to this module.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import utcnow
from app.modules.matching.models import CandidateMatch
from app.modules.offers import JobOfferRepository

logger = get_logger("matching.purge")


async def purge_old_offers(session: AsyncSession, *, dry_run: bool = False) -> int:
    """Number of offers deleted (or, with `dry_run`, that would be)."""
    cutoff = utcnow() - timedelta(days=get_settings().OFFERS_RETENTION_DAYS)
    repo = JobOfferRepository(session)
    count = await repo.purge_older_than(
        cutoff,
        keep=select(CandidateMatch.job_offer_id),  # type: ignore[arg-type]
        dry_run=dry_run,
    )
    if not dry_run:
        await session.commit()
        logger.info("offers_purged", deleted=count)
    return count
