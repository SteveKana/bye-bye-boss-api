"""Monitoring module -- the admin dashboard's back end.

Reads the other modules' data (through the read-only handles of queries.py),
records what nothing recorded before (page views, AI token usage, technical
incidents -- fed by the events of app/core/monitoring_events.py) and sends the
admin's announcements. Every route is admin-only except the tracking
endpoints (any logged-in user, or anonymous for browser errors) and the
unsubscribe link.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register models (Alembic), event listeners, jobs.
from app.modules.monitoring import jobs as jobs  # noqa: F401
from app.modules.monitoring import listeners as listeners  # noqa: F401
from app.modules.monitoring import models as models  # noqa: F401
from app.modules.monitoring.routes.v1 import (
    admin_routes,
    track_routes,
    unsubscribe_routes,
)

_router = APIRouter()
_router.include_router(admin_routes.router)
_router.include_router(track_routes.router)
_router.include_router(unsubscribe_routes.router)

module = Module(
    name="monitoring",
    router=_router,
    order=90,
    depends_on=["auth", "mailer"],
    tags=["monitoring"],
)

__all__ = ["module"]
