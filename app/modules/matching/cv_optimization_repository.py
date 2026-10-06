from __future__ import annotations

import uuid

from app.core.repository import BaseRepository
from app.modules.matching.cv_optimization_models import CVOptimization


class CVOptimizationRepository(BaseRepository[CVOptimization]):
    model = CVOptimization

    async def get_by_match(
        self, candidate_match_id: uuid.UUID
    ) -> CVOptimization | None:
        return await self.find_one(candidate_match_id=candidate_match_id)
