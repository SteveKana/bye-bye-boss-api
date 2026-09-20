from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import desc

from app.core.repository import BaseRepository
from app.modules.offers.models import JobOffer


class JobOfferRepository(BaseRepository[JobOffer]):
    model = JobOffer

    async def get_by_source_external_id(
        self, source: str, external_id: str
    ) -> JobOffer | None:
        return await self.find_one(source=source, external_id=external_id)

    async def list_recent(self, *, since: datetime) -> Sequence[JobOffer]:
        """Offers ingested on or after `since`, most recently published
        first -- used by the matching module to bound the pool of offers it
        even considers shortlisting (see MATCHING_MAX_OFFER_POOL_DAYS)."""
        stmt = (
            self._base_select()
            .where(JobOffer.created_at >= since)
            .order_by(desc(JobOffer.published_at))
        )
        return (await self.session.exec(stmt)).all()
