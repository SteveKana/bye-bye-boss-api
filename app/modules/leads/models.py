from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime
from sqlmodel import Field

from app.core.models import BaseModel


class Lead(BaseModel, table=True):
    """A waitlist signup collected on the public landing page."""

    __tablename__ = "leads"

    email: str = Field(index=True, unique=True, nullable=False)
    # Locale the visitor used, so the acknowledgement is sent in their language.
    locale: str = Field(default="fr", nullable=False)
    # Free-form acquisition origin (landing section, campaign, utm...).
    source: str | None = Field(default=None)
    # When the launch invitation was queued for this lead (None = never), so
    # re-running the import can never send a second invitation.
    invited_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
