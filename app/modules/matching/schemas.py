from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.modules.matching.models import ApplicationStatus
from app.modules.offers import JobOfferRead


class CandidateMatchRead(BaseModel):
    id: uuid.UUID
    company_name: str
    career_score: int
    ats_score: int
    ats_potential: int
    blocking_message: str
    analysis: dict
    regret_availability: str
    regret_score: int | None
    computed_at: datetime
    application_status: ApplicationStatus
    application_status_updated_at: datetime | None
    offer: JobOfferRead


class ApplicationStatusUpdate(BaseModel):
    application_status: ApplicationStatus


class CVOptimizationRead(BaseModel):
    id: uuid.UUID
    headline: str
    summary: str
    summary_why: str
    # Kept as raw JSON blobs, same convention as CandidateMatchRead.analysis
    # above: this is LLM-derived content the frontend renders directly
    # rather than a strictly-typed contract, and it's already validated once
    # on the way in (see cv_optimization_schema.py) before being reconciled
    # and stored.
    experiences: list[dict]
    skills: list[dict]
    advice: str
    computed_at: datetime
    confirmed_at: datetime | None
    # Reused straight from the already-computed match, never a separate
    # number the optimization call invents itself: ats_potential is already
    # defined (see matching/prompt.py's ETAPE 9) as "the ATS score
    # achievable via wording/presentation improvements alone, without
    # inventing skills" -- exactly what this feature promises, so the two
    # stay consistent with what the opportunity page already shows.
    ats_score_before: int
    ats_score_after: int
