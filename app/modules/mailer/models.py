from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime
from sqlmodel import Field

from app.core.models import BaseModel


class EmailStatus(enum.StrEnum):
    pending = "pending"
    sent = "sent"
    failed = "failed"


class EmailMessage(BaseModel, table=True):
    """Outbox row. Producers write it inside their own transaction; the queue
    worker picks it up and actually sends it."""

    __tablename__ = "email_messages"

    to_email: str = Field(index=True, nullable=False)
    subject: str = Field(nullable=False)
    body_text: str = Field(nullable=False)
    body_html: str | None = Field(default=None)

    status: str = Field(default=EmailStatus.pending.value, index=True, nullable=False)
    attempts: int = Field(default=0, nullable=False)
    last_error: str | None = Field(default=None)
    sent_at: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        nullable=True,
    )
