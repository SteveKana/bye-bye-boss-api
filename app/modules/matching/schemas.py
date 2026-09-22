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
