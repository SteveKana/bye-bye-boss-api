from __future__ import annotations

from app.core.repository import BaseRepository
from app.modules.offers.models import JobOffer


class JobOfferRepository(BaseRepository[JobOffer]):
    model = JobOffer

    async def get_by_source_external_id(
        self, source: str, external_id: str
    ) -> JobOffer | None:
        return await self.find_one(source=source, external_id=external_id)
