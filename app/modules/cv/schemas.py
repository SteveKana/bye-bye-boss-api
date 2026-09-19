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


class CandidateProfileRead(BaseSchema):
    id: uuid.UUID
    status: str
    updated_at: datetime
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
    contract_types: list[str]
    remote_preferences: list[str]
    mobility: str | None
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


ContractType = Literal["CDI", "CDD", "Freelance", "Intérim"]
RemotePreference = Literal["Sur site", "Hybride", "Full remote"]
Mobility = Literal["France entière", "Région uniquement", "Ville uniquement"]


class PreferencesUpdate(BaseSchema):
    contract_types: list[ContractType] = Field(min_length=1)
    remote_preferences: list[RemotePreference] = Field(min_length=1)
    mobility: Mobility
    salary_target: int | None = Field(default=None, ge=0)
    # Only meaningful when "Freelance" is among contract_types, but not
    # enforced server-side -- the client hides the field otherwise, and
    # rejecting a stray value would just be an extra way to fail a request
    # for no real benefit.
    daily_rate: int | None = Field(default=None, ge=0)
