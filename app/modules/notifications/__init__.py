"""Notifications module — public surface.

"Le brief quotidien" -- once a day, each candidate with a complete profile
and at least one enabled channel gets their best new offers (never a
repeat, see NotificationBriefEntry) by email, Discord (a webhook the
candidate creates themselves), and/or WhatsApp (Meta Business API --
requires app-wide setup, see channels/whatsapp_channel.py; skipped
gracefully while unconfigured). Depends on `cv`/`matching`/`offers` for the
candidate/match/offer data a brief is built from, `auth` for the account
being notified, and `mailer` for the email channel itself.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register the models (Alembic) and the scheduled job.
from app.modules.notifications import jobs as jobs  # noqa: F401
from app.modules.notifications import models as models  # noqa: F401
from app.modules.notifications.repository import (
    NotificationBriefEntryRepository,
    NotificationPreferenceRepository,
)
from app.modules.notifications.routes.v1 import notification_routes
from app.modules.notifications.schemas import NotificationPreferenceRead
from app.modules.notifications.service import (
    DailyBriefService,
    NotificationPreferenceService,
)

_router = APIRouter()
_router.include_router(notification_routes.router)

module = Module(
    name="notifications",
    router=_router,
    order=60,
    depends_on=["auth", "mailer", "cv", "matching", "offers"],
    tags=["notifications"],
)

__all__ = [
    "module",
    "NotificationPreferenceRepository",
    "NotificationBriefEntryRepository",
    "NotificationPreferenceRead",
    "NotificationPreferenceService",
    "DailyBriefService",
]
