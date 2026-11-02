"""Market-watch module (veille marché).

Reads the offers already ingested, files each under a precise specialty from
a closed list and extracts the hard skills it asks for (AI, cheap model), and
keeps the weekly counts as a history -- offers themselves are deleted after
15 days, the counts are not. From that history it builds a digest e-mail per
candidate profile; for now only an admin can send it (see
routes/v1/admin_routes.py).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register models (Alembic) and the scheduled jobs.
from app.modules.market import jobs as jobs  # noqa: F401
from app.modules.market import models as models  # noqa: F401
from app.modules.market.routes.v1 import admin_routes

_router = APIRouter()
_router.include_router(admin_routes.router)

module = Module(
    name="market",
    router=_router,
    order=95,
    depends_on=["auth", "cv", "mailer", "offers"],
    tags=["market"],
)

__all__ = ["module"]
