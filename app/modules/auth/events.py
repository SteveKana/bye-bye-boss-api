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


class UserDeletionRequested(Event):
    """Emitted by AuthService.delete_account BEFORE the user row is removed
    (with `raise_on_error=True`): every module owning data about this user
    purges it in reaction -- see each module's `listeners.py`. The account
    is only deleted once all of them succeeded, so a failed purge leaves the
    account in place and the request can simply be retried."""

    user_id: uuid.UUID
