from __future__ import annotations

import uuid

from app.core.events import Event


class UserRegistered(Event):
    user_id: uuid.UUID
    email: str
    first_name: str | None = None
    last_name: str | None = None


class UserDeleted(Event):
    user_id: uuid.UUID
