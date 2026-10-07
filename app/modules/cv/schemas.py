from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import Field

from app.core.schemas import BaseSchema

AvailabilityStatusLiteral = Literal["immediate", "date", "notice", "unavailable"]


class ExperienceItem(BaseSchema):
    title: str = ""
    company: str = ""
    period: str = ""
    description: str = ""
    tools: list[str] = Field(default_factory=list)


class FormationItem(BaseSchema):
    title: str = ""
    school_period: str = ""


class LanguageItem(BaseSchema):
    name: str = ""
    level: str = ""


class CertificationItem(BaseSchema):
    title: str = ""
    issuer_period: str = ""


class SkillCategoryItem(BaseSchema):
    category: str = ""
    skills: list[str] = Field(default_factory=list)


class CandidateProfileRead(BaseSchema):
    id: uuid.UUID
    status: str
    updated_at: datetime
    # When the CV itself was last parsed (see service.import_cv) -- distinct
    # from `updated_at` above, which bumps on ANY change to this row
    # (preferences, verification corrections...). The profile page's
    # "Dernière mise à jour" next to the CV download button means "last time
    # we reprocessed your CV", so it reads this field, not `updated_at`.
    cv_analyzed_at: datetime | None
    # Non-null once the verification step (or a later /profile edit, which
    # reuses the same endpoint) has been saved at least once -- lets the
    # frontend tell "just imported, not yet verified" apart from "verified,
    # preferences not saved" (both are status == "draft"). See
    # CvService.apply_verification and models.py's docstring on the column.
    verification_completed_at: datetime | None
    first_name: str | None
    last_name: str | None
    headline: str | None
    email: str | None
    location: str | None
    availability_status: str
    availability_date: date | None
    notice_period_months: int | None
    total_experience: str | None
    experiences: list[ExperienceItem]
    skills: list[str]
    formations: list[FormationItem]
    languages: list[LanguageItem]
    certifications: list[CertificationItem]
    # Synthesized from the CV by the LLM -- not user-editable, so these three
    # are absent from CandidateProfileUpdate below.
    professional_summary: str | None
    identified_roles: list[str]
    domains: list[str]
    skill_categories: list[SkillCategoryItem]
    contract_types: list[str]
    remote_preferences: list[str]
    mobility: str | None
    mobility_region: str | None
    mobility_regions: list[str]
    include_unknown_region: bool
    preferences_saved_at: datetime | None
    salary_target: int | None
    daily_rate: int | None
    cv_filename: str | None


class CandidateProfileUpdate(BaseSchema):
    """Body for the verification step — the user's corrections to the
    auto-extracted data. Every field is optional so the client can send only
    what changed, but in practice the verification screen resubmits the
    whole block."""

    first_name: str | None = None
    last_name: str | None = None
    headline: str | None = None
    email: str | None = None
    location: str | None = None
    availability_status: AvailabilityStatusLiteral | None = None
    availability_date: date | None = None
    notice_period_months: int | None = Field(default=None, ge=0, le=24)
    total_experience: str | None = None
    experiences: list[ExperienceItem] | None = None
    skills: list[str] | None = None
    formations: list[FormationItem] | None = None
    languages: list[LanguageItem] | None = None
    certifications: list[CertificationItem] | None = None


ContractType = Literal["CDI", "CDD", "Freelance", "Intérim", "Stage", "Alternance"]
RemotePreference = Literal["Sur site", "Hybride", "Full remote"]
Mobility = Literal["France entière", "Région uniquement", "Ville uniquement"]
# The 18 French régions (13 metropolitan + 5 overseas) -- the candidate
# picks one explicitly (see PreferencesForm.vue) rather than us guessing it
# from the free-text `location` extracted off their CV, which has no
# guaranteed format to parse a région out of reliably.
MobilityRegion = Literal[
    "Auvergne-Rhône-Alpes",
    "Bourgogne-Franche-Comté",
    "Bretagne",
    "Centre-Val de Loire",
    "Corse",
    "Grand Est",
    "Hauts-de-France",
    "Île-de-France",
    "Normandie",
    "Nouvelle-Aquitaine",
    "Occitanie",
    "Pays de la Loire",
    "Provence-Alpes-Côte d'Azur",
    "Guadeloupe",
    "Martinique",
    "Guyane",
    "La Réunion",
    "Mayotte",
]


class PreferencesUpdate(BaseSchema):
    """The search preferences (Steve, 2026-10-07). Every list may be empty,
    which means "no restriction": all contracts, all work modes, France
    entière."""

    contract_types: list[ContractType] = Field(default_factory=list)
    remote_preferences: list[RemotePreference] = Field(default_factory=list)
    mobility_regions: list[MobilityRegion] = Field(default_factory=list)
    include_unknown_region: bool = True
    # Annual gross minimum. Offers that state no salary are never excluded.
    salary_target: int | None = Field(default=None, ge=0)
    # Taux Journalier Moyen, for freelance missions. Only pre-fills the TJM
    # filter on the offer pages: a day rate is read from free text and is not
    # reliable enough to exclude offers before the analysis.
    daily_rate: int | None = Field(default=None, ge=0)
