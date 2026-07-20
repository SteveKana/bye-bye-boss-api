from __future__ import annotations

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
