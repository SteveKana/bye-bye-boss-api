"""Market-watch tables.

* `offer_market_tags`: what the AI read in one offer (specialty + skills).
  Dies with the offer (cascade) -- the weekly counts below are what is kept.
* `profile_market_tags`: the specialty filed for a candidate profile.
* `market_group_weekly` / `market_skill_weekly`: the history. One row per
  week, specialty (and skill). Counts only -- no offer text, no company, no
  link, nothing about a candidate.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Column, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel

_JsonColumn = JSON().with_variant(JSONB(), "postgresql")

# "" in the `specialty` columns means "the whole family".
FAMILY_LEVEL = ""


class OfferMarketTag(BaseModel, table=True):
    __tablename__ = "offer_market_tags"

    job_offer_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("job_offers.id", ondelete="CASCADE"),
            unique=True,
            index=True,
            nullable=False,
        )
    )
    family: str = Field(index=True, nullable=False)
    specialty: str | None = Field(default=None)
    # list[{"name": "Jira", "key": "jira", "category": "outil"}]
    skills: list = Field(default_factory=list, sa_column=Column(_JsonColumn))
    model: str = Field(default="", nullable=False)


class ProfileMarketTag(BaseModel, table=True):
    __tablename__ = "profile_market_tags"

    profile_id: uuid.UUID = Field(unique=True, index=True, nullable=False)
    family: str = Field(nullable=False)
    specialty: str | None = Field(default=None)
    model: str = Field(default="", nullable=False)


class MarketGroupWeekly(BaseModel, table=True):
    __tablename__ = "market_group_weekly"
    __table_args__ = (
        UniqueConstraint(
            "week_start", "family", "specialty", name="uq_market_group_weekly"
        ),
    )

    week_start: date = Field(index=True, nullable=False)  # a Monday (Paris)
    family: str = Field(nullable=False)
    specialty: str = Field(default=FAMILY_LEVEL, nullable=False)
    offers_total: int = Field(default=0, nullable=False)
    # False for the week in progress: its counts are still growing.
    is_complete: bool = Field(default=False, nullable=False)


class MarketSkillWeekly(BaseModel, table=True):
    __tablename__ = "market_skill_weekly"
    __table_args__ = (
        UniqueConstraint(
            "week_start",
            "family",
            "specialty",
            "skill_key",
            name="uq_market_skill_weekly",
        ),
    )

    week_start: date = Field(index=True, nullable=False)
    family: str = Field(nullable=False)
    specialty: str = Field(default=FAMILY_LEVEL, nullable=False)
    skill_key: str = Field(nullable=False)
    skill_label: str = Field(nullable=False)
    category: str = Field(nullable=False)
    offers_with_skill: int = Field(default=0, nullable=False)


class MarketDigestOptOut(BaseModel, table=True):
    """Users who asked not to receive the market-watch digest (the link at the
    bottom of every digest). Separate from the announcements opt-out: leaving
    one list says nothing about the other."""

    __tablename__ = "market_digest_optouts"

    user_id: uuid.UUID = Field(index=True, unique=True, nullable=False)
