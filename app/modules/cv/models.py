from __future__ import annotations

import enum
import uuid

from sqlalchemy import JSON, Column
from sqlmodel import Field

from app.core.models import BaseModel


class ProfileStatus(enum.StrEnum):
    draft = "draft"  # CV imported and parsed, not yet confirmed by the user
    complete = "complete"  # user validated info + preferences (onboarding done)


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
    availability: str | None = Field(default=None)

    # -- Expérience
    total_experience: str | None = Field(default=None)
    # list[{title, company, period, description, tools: list[str]}]
    experiences: list = Field(default_factory=list, sa_column=Column(JSON))
    skills: list = Field(default_factory=list, sa_column=Column(JSON))
    # list[{title, school_period}]
    formations: list = Field(default_factory=list, sa_column=Column(JSON))
    # list[{name, level}]
    languages: list = Field(default_factory=list, sa_column=Column(JSON))
    # list[{title, issuer_period}]
    certifications: list = Field(default_factory=list, sa_column=Column(JSON))

    # -- Préférences (étape 3 de l'onboarding)
    contract_types: list = Field(default_factory=list, sa_column=Column(JSON))
    remote_preferences: list = Field(default_factory=list, sa_column=Column(JSON))
    mobility: str | None = Field(default=None)
    salary_target: int | None = Field(default=None)

    # Texte brut extrait du fichier, conservé pour permettre un nouveau
    # passage de parsing (ex. changement de prompt) sans redemander le CV.
    raw_text: str | None = Field(default=None)
