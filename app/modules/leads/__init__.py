"""Leads module — public surface.

Waitlist capture for the marketing landing page. Depends on `mailer` to queue
the acknowledgement and on `auth` for the admin-only listing.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register the model so Alembic sees it.
from app.modules.leads import models as models  # noqa: F401
from app.modules.leads.events import LeadCaptured
from app.modules.leads.routes.v1 import lead_routes
from app.modules.leads.schemas import LeadRead

_router = APIRouter()
_router.include_router(lead_routes.router)

module = Module(
    name="leads",
    router=_router,
    order=30,
    depends_on=["mailer", "auth"],
    tags=["leads"],
)

__all__ = [
    "module",
    "LeadCaptured",
    "LeadRead",
]
