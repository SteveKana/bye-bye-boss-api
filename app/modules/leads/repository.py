from __future__ import annotations

from app.core.repository import BaseRepository
from app.modules.leads.models import Lead


class LeadRepository(BaseRepository[Lead]):
    model = Lead

    async def get_by_email(self, email: str) -> Lead | None:
        return await self.find_one(email=email)
