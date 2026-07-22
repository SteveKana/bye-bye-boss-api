from __future__ import annotations

from collections.abc import Sequence

from app.core.repository import BaseRepository
from app.modules.mailer.models import EmailMessage, EmailStatus


class EmailRepository(BaseRepository[EmailMessage]):
    model = EmailMessage

    async def list_pending(self, limit: int) -> Sequence[EmailMessage]:
        """Oldest pending messages first, so the queue drains in order."""
        return await self.list(
            filters={"status": EmailStatus.pending.value},
            order_by=EmailMessage.created_at,
            limit=limit,
        )
