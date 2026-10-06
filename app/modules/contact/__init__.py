"""Contact module — public surface.

The public contact form (/contact on the site): a visitor writes a message and
it is delivered by email to `CONTACT_RECIPIENT_EMAIL`. Depends on `mailer` to
queue the message. Nothing is stored in the database: the email is the record.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module
from app.modules.contact.routes.v1 import contact_routes

_router = APIRouter()
_router.include_router(contact_routes.router)

module = Module(
    name="contact",
    router=_router,
    order=31,
    depends_on=["mailer"],
    tags=["contact"],
)

__all__ = ["module"]
