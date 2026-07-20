"""Mailer module — public surface.

Queued outgoing mail. Other modules import ONLY from here:

    from app.modules.mailer import MailerGateway
"""

from __future__ import annotations

from app.core.module import Module

# Import side effects: register the model (Alembic) and the queue worker.
from app.modules.mailer import jobs as jobs  # noqa: F401
from app.modules.mailer import models as models  # noqa: F401
from app.modules.mailer.gateway import MailerGateway
from app.modules.mailer.models import EmailStatus

module = Module(
    name="mailer",
    order=20,
    tags=["mailer"],
)

__all__ = [
    "module",
    "MailerGateway",
    "EmailStatus",
]
