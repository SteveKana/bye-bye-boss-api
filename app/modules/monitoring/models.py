from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel

_JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class IncidentStatus(enum.StrEnum):
    new = "new"
    in_progress = "in_progress"
    resolved = "resolved"


class PageView(BaseModel, table=True):
    """One page opened by a logged-in user (see track_routes.py). First-party
    only: no cookie, no third-party tool; purged after
    MONITORING_PAGE_VIEW_RETENTION_DAYS (see jobs.py)."""

    __tablename__ = "monitoring_page_views"

    user_id: uuid.UUID = Field(index=True, nullable=False)
    # Normalised route ("/opportunites", "/opportunites/:id"), never a raw URL
    # with query string.
    path: str = Field(index=True, nullable=False)


class AiUsage(BaseModel, table=True):
    """Tokens consumed by one OpenAI batch result file. The cost is computed
    when displayed, from the prices in settings -- never stored, so a price
    change corrects the whole history."""

    __tablename__ = "monitoring_ai_usage"

    stage: str = Field(index=True, nullable=False)
    model: str = Field(nullable=False)
    requests: int = Field(default=0, nullable=False)
    input_tokens: int = Field(default=0, nullable=False)
    output_tokens: int = Field(default=0, nullable=False)


class Incident(BaseModel, table=True):
    """A technical problem met by users or by the platform. Identical
    occurrences share a `fingerprint` and are grouped in one row."""

    __tablename__ = "monitoring_incidents"

    fingerprint: str = Field(index=True, unique=True, nullable=False)
    kind: str = Field(index=True, nullable=False)
    title: str = Field(nullable=False)
    context: str = Field(default="")
    origin: str = Field(default="")
    technical_cause: str = Field(default="")
    user_message: str | None = Field(default=None)
    status: str = Field(default=IncidentStatus.new.value, index=True, nullable=False)
    count: int = Field(default=1, nullable=False)
    # Distinct affected users (ids, capped) and the latest occurrences (ISO
    # strings, newest first, capped).
    user_ids: list = Field(default_factory=list, sa_column=Column(_JsonColumn))
    occurrences: list = Field(default_factory=list, sa_column=Column(_JsonColumn))
    first_seen: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    last_seen: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    resolved_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class Announcement(BaseModel, table=True):
    """An e-mail sent by the admin to a group of users -- or, with
    `is_test`, to the admin alone (a real send must be preceded by a test of
    the very same content: see announcements.py)."""

    __tablename__ = "monitoring_announcements"

    admin_id: uuid.UUID = Field(index=True, nullable=False)
    subject: str = Field(nullable=False)
    body: str = Field(nullable=False)
    content_hash: str = Field(index=True, nullable=False)
    audience: str = Field(nullable=False)
    is_test: bool = Field(default=False, index=True, nullable=False)
    sent: int = Field(default=0, nullable=False)
    skipped_unsubscribed: int = Field(default=0, nullable=False)
    failed: int = Field(default=0, nullable=False)


class AnnouncementOptOut(BaseModel, table=True):
    """Users who asked not to receive announcements (the link at the bottom of
    every announcement)."""

    __tablename__ = "monitoring_announcement_optouts"

    user_id: uuid.UUID = Field(index=True, unique=True, nullable=False)
