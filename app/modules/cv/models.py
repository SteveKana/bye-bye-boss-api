from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import JSON, Column
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
    email: str | None = Field(default=None)
    location: str | None = Field(default=None)
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

    # -- Préférences (étape 3 de l'onboarding)
    contract_types: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )
    remote_preferences: list = Field(
        default_factory=list, sa_column=Column(_JsonListColumn)
    )
    mobility: str | None = Field(default=None)
    salary_target: int | None = Field(default=None)

    # Texte brut extrait du fichier, conservé pour permettre un nouveau
    # passage de parsing (ex. changement de prompt) sans redemander le CV.
    raw_text: str | None = Field(default=None)
