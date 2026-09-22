from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel

# JSONB on Postgres, plain JSON elsewhere -- same pattern as
# app/modules/cv/models.py and app/modules/offers/models.py.
_JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class ApplicationStatus(enum.StrEnum):
    """Where a candidate stands on a given offer -- entirely self-reported
    (or self-corrected, see below), since MatchCareer never sees what
    happens on the employer's/aggregator's own site.

    `not_applied` is the default for every match; it's what keeps a match
    out of the "Candidatures" list (see matching_routes.list_applications).
    `applied` is normally set automatically -- see
    routes/v1/matching_routes.mark_applied, called the moment the
    candidate clicks "Voir l'offre" on the opportunity page, on the
    (deliberate) assumption that clicking through is itself the signal of
    intent, so nothing is asked of the candidate for the common case. Every
    other transition (interview/offer/rejected/withdrawn), and correcting a
    wrong `applied` back to `not_applied`, is explicit -- see
    update_application_status.
    """

    not_applied = "not_applied"
    applied = "applied"
    interview = "interview"
    offer = "offer"
    rejected = "rejected"
    withdrawn = "withdrawn"


class CandidateMatch(BaseModel, table=True):
    """A computed match between one candidate profile and one job offer.

    One row per (candidate_profile, job_offer) pair, refreshed in place
    rather than duplicated -- see MatchingService._upsert. Computed entirely
    in the background (see jobs.py): the dashboard only ever reads rows this
    table already has, so opening it never waits on an LLM call.

    The Regret Index is intentionally NOT computed here: MatchCareer defers
    it until a legitimate employee-review data source exists (see the
    `matching` module's docstring) -- `regret_availability` is always
    "unavailable" for now, never a fabricated score.
    """

    __tablename__ = "candidate_matches"
    __table_args__ = (
        UniqueConstraint(
            "candidate_profile_id",
            "job_offer_id",
            name="uq_candidate_matches_profile_offer",
        ),
    )

    candidate_profile_id: uuid.UUID = Field(index=True, nullable=False)
    job_offer_id: uuid.UUID = Field(index=True, nullable=False)

    company_name: str = Field(default="")
    career_score: int = Field(nullable=False)
    ats_score: int = Field(nullable=False)
    ats_potential: int = Field(nullable=False)
    blocking_message: str = Field(default="")

    # Full LLMAnalysis payload (matches, ats_gaps, actions, explanations,
    # skills...) -- kept as one JSON blob rather than normalized into more
    # tables, mirroring candidate_profiles' own JSON list fields. career_score
    # /ats_score/ats_potential are promoted to real columns above so the
    # dashboard's "top matches" query can sort/filter in SQL.
    analysis: dict = Field(default_factory=dict, sa_column=Column(_JsonColumn))

    # Always "unavailable" until a real review data source is wired in.
    regret_availability: str = Field(default="unavailable", nullable=False)
    regret_score: int | None = Field(default=None)

    computed_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))

    # See ApplicationStatus's docstring. Never touched by MatchingService's
    # re-scoring upsert (see service.py's `values` dict) -- a fresh LLM
    # score must never reset a candidate's declared application progress.
    application_status: str = Field(
        default=ApplicationStatus.not_applied.value, nullable=False, index=True
    )
    application_status_updated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
