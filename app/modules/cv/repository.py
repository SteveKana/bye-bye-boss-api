from __future__ import annotations

import uuid

from app.core.repository import BaseRepository
from app.modules.cv.models import CandidateProfile


class CandidateProfileRepository(BaseRepository[CandidateProfile]):
    model = CandidateProfile

    async def get_by_user(self, user_id: uuid.UUID) -> CandidateProfile | None:
        return await self.find_one(user_id=user_id)
