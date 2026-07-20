from __future__ import annotations

import uuid

from app.core.events import Event


class LeadCaptured(Event):
    lead_id: uuid.UUID
    email: str
    locale: str
