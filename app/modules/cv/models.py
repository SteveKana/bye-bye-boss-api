from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel


class ProfileStatus(enum.StrEnum):
    draft = "draft"  # CV imported and parsed, not yet confirmed by the user
    complete = "complete"  # user validated info + preferences (onboarding done)


class AvailabilityStatus(enum.StrEnum):
    immediate = "immediate"
    date = "date"  # specific start date -- see availability_date
    notice = "notice"  # currently employed, serving notice -- see notice_period_months
    unavailable = "unavailable"


# JSONB on Postgres (compact, indexable); plain JSON elsewhere (e.g. SQLite
# in tests) since JSONB has no SQLite equivalent.
_JsonListColumn = JSON().with_variant(JSONB(), "postgresql")


class CandidateProfile(BaseModel, table=True):
    """A candidate's profile, seeded by parsing an uploaded CV.

    One profile per user (`user_id` is unique). The onboarding wizard fills
    it in two passes: `POST /cv/upload` creates it as a draft from the raw
    extraction, `PUT /cv/profile` applies the user's corrections, and
    `PUT /cv/profile/preferences` completes it. Structured, list-shaped CV
    content (experiences, formations...) has no fixed cardinality and is
    edited as a whole block by the client, so it is stored as JSON rather
    than normalized into child tables.
    """

    __tablename__ = "candidate_profiles"

    user_id: uuid.UUID = Field(index=True, unique=True, nullable=False)
    status: str = Field(default=ProfileStatus.draft.value, nullable=False)

    # -- Informations personnelles
    first_name: str | None = Field(default=None)
    last_name: str | None = Field(default=None)
    # Headline (job title shown under the name). Seeded from the most
    # recent experience on first import (see service.import_cv), then
    # independently editable and preserved across CV re-imports -- once
    # customized, it's the user's wording to keep, not a raw CV fact.
    headline: str | None = Field(default=None)
    email: str | None = Field(default=None)
    location: str | None = Field(default=None)
    # A short, synthesized 1-2 sentence summary of the candidate's
    # professional profile -- like headline/experiences, always refreshed
    # from the latest CV import rather than user-editable, since it's a
    # synthesis of the CV content rather than a fact the user states.
    professional_summary: str | None = Field(default=None)
    # Availability is a live preference, not really a CV fact -- kept
    # structured rather than free text, and only defaulted on first import
    # (see service.import_cv), never overwritten by a CV re-import.
    # availability_date only applies when status == "date"; notice_period_months
    # only applies when status == "notice" -- the other is cleared whenever
    # one is set (see service.apply_verification).
    availability_status: str = Field(
        default=AvailabilityStatus.immediate.value, nullable=False
    )
    availability_date: date | None = Field(default=None)
    notice_period_months: int | None = Field(default=None)

    # -- Expérience
    total_experience: str | None = Field(default=None)
    # list[{title, company, period, description, tools: list[str]}]
    experiences: list = Field(default_factory=list, sa_column=Column(_JsonListColumn))
    skills: list = Field(default_factory=list, sa_column=Column(_JsonListColumn))
    # list[{title, school_period}]
    formations: list = Field(default_factory=list, sa_column=Column(_JsonListColumn))
    # list[{name, level}]
    languages: list = Field(default_factory=list, sa_column=Column(_JsonListColumn))
    # list[{title, issuer_period}]
    certifications: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )
    # Job titles/functions this profile is suited for, and the business
    # domains it shows experience in -- both inferred by the LLM from the
    # whole CV rather than copied from any single field, and, like the
    # fields above, refreshed on every re-import rather than user-editable.
    identified_roles: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )
    domains: list = Field(default_factory=list, sa_column=Column(_JsonListColumn))
    # list[{category, skills: list[str]}] -- the same skills as `skills`
    # above, grouped under LLM-chosen category names for display. `skills`
    # itself stays a flat list since that's what matching logic keys off of;
    # this is a presentation-only view over the same underlying data.
    skill_categories: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )

    # -- Préférences (étape 3 de l'onboarding)
    contract_types: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )
    remote_preferences: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )
    mobility: str | None = Field(default=None)
    # Only meaningful when mobility == "Région uniquement" -- which of the
    # 18 French régions the candidate picked (see schemas.MobilityRegion).
    mobility_region: str | None = Field(default=None)
    salary_target: int | None = Field(default=None)
    # Taux Journalier Moyen -- daily rate for candidates open to freelance
    # missions. Independent of salary_target: a candidate can look for both
    # a permanent role and freelance work at once, so neither field implies
    # or replaces the other.
    daily_rate: int | None = Field(default=None)

    # When the CV was last actually (re-)parsed (see service.import_cv) --
    # deliberately separate from BaseModel's `updated_at`, which the DB
    # bumps on ANY change to this row (saving preferences, verification
    # edits...). Without this, "Dernière mise à jour" on the profile page
    # would read as "you just updated your CV" whenever the candidate had
    # merely saved a preference.
    cv_analyzed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )

    # Texte brut extrait du fichier, conservé pour permettre un nouveau
    # passage de parsing (ex. changement de prompt) sans redemander le CV.
    raw_text: str | None = Field(default=None)

    # Embedding vector of raw_text (see core/embeddings.py), used by
    # matching/shortlist.py to rank the offer pool by semantic similarity.
    # Computed lazily on the profile's first matching run (see
    # MatchingService._run_for_profile), not here at import time -- and
    # explicitly cleared (see service.import_cv) whenever raw_text changes,
    # so a stale embedding is never scored against a new CV.
    embedding: list[float] | None = Field(
        default=None, sa_column=Column(_JsonListColumn)
    )

    # The original uploaded file's bytes are saved to disk (see
    # extraction.CV_UPLOAD_DIR), named after this profile's id -- these two
    # fields are what's needed to serve it back with the right filename and
    # content type on download.
    cv_filename: str | None = Field(default=None)
    cv_content_type: str | None = Field(default=None)
