from __future__ import annotations

import uuid
from collections.abc import Sequence

from app.core.repository import BaseRepository
from app.modules.notifications.models import (
    NotificationBriefEntry,
    NotificationPreference,
)


class NotificationPreferenceRepository(BaseRepository[NotificationPreference]):
    model = NotificationPreference

    async def get_by_user(self, user_id: uuid.UUID) -> NotificationPreference | None:
        return await self.find_one(user_id=user_id)


class NotificationBriefEntryRepository(BaseRepository[NotificationBriefEntry]):
    model = NotificationBriefEntry

    async def list_for_user(
        self, user_id: uuid.UUID
    ) -> Sequence[NotificationBriefEntry]:
        return await self.list(filters={"user_id": user_id})

    async def sent_match_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """Every match already sent to this user, in any past brief -- see
        NotificationBriefEntry's docstring for why this is a dedicated
        ledger rather than a flag on CandidateMatch itself."""
        entries = await self.list_for_user(user_id)
        return {entry.candidate_match_id for entry in entries}
