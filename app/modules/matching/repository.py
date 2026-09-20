from __future__ import annotations

import uuid

from sqlmodel import desc

from app.core.repository import BaseRepository
from app.modules.matching.models import CandidateMatch


class CandidateMatchRepository(BaseRepository[CandidateMatch]):
    model = CandidateMatch

    async def get_by_profile_and_offer(
        self, candidate_profile_id: uuid.UUID, job_offer_id: uuid.UUID
    ) -> CandidateMatch | None:
        return await self.find_one(
            candidate_profile_id=candidate_profile_id, job_offer_id=job_offer_id
        )

    async def list_top_for_profile(
        self, candidate_profile_id: uuid.UUID, *, limit: int = 20
    ):
        return await self.list(
            filters={"candidate_profile_id": candidate_profile_id},
            order_by=desc(CandidateMatch.career_score),
            limit=limit,
        )
