"""Notification preferences and delivery tracking for the daily brief.

Two tables, both self-contained here rather than mutating `auth.User` or
`matching.CandidateMatch` -- same "own table, keyed by the id, not a bolted-
on column on someone else's model" convention as `matching.CVOptimization`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel

# JSONB on Postgres, plain JSON elsewhere -- same pattern as
# app/modules/matching/models.py.
_JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class NotificationPreference(BaseModel, table=True):
    """One row per user -- which channels the daily brief goes out on, and
    the contact detail each one needs. Email is on by default (it needs no
    extra detail: the account's own address); Discord/WhatsApp are off by
    default and need their contact detail filled in before they can be
    turned on (enforced in NotificationPreferenceService, not here)."""

    __tablename__ = "notification_preferences"

    user_id: uuid.UUID = Field(index=True, unique=True, nullable=False)

    email_enabled: bool = Field(default=True, nullable=False)

    # A webhook URL the candidate creates themselves on a Discord channel
    # they control (Channel Settings -> Integrations -> Webhooks) -- no
    # bot/OAuth flow needed on our side, see channels/discord_channel.py.
    discord_enabled: bool = Field(default=False, nullable=False)
    discord_webhook_url: str | None = Field(default=None)

    # Requires WHATSAPP_* to be configured app-wide (Meta Business API --
    # see app/core/config.py) on top of the user's own opt-in below; see
    # channels/whatsapp_channel.py for what happens while that's unset.
    whatsapp_enabled: bool = Field(default=False, nullable=False)
    whatsapp_phone_number: str | None = Field(default=None)


class NotificationBriefEntry(BaseModel, table=True):
    """One row per (user, match) ever included in a daily brief -- the
    dedup ledger that keeps an offer from ever being sent to the same
    candidate twice, no matter how many days it stays in their top-N.
    `channels_sent` is for observability only (which channels actually
    accepted delivery); a match is logged here once attempted regardless of
    per-channel success, so a flaky webhook can't cause a resend storm."""

    __tablename__ = "notification_brief_entries"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "candidate_match_id", name="uq_brief_entries_user_match"
        ),
    )

    user_id: uuid.UUID = Field(index=True, nullable=False)
    candidate_match_id: uuid.UUID = Field(index=True, nullable=False)
    sent_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    # list[str], e.g. ["email", "discord"] -- see docstring above.
    channels_sent: list = Field(default_factory=list, sa_column=Column(_JsonColumn))
