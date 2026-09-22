from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel

# JSONB on Postgres, plain JSON elsewhere -- same pattern as
# app/modules/matching/models.py.
_JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class CVOptimization(BaseModel, table=True):
    """A CV rewritten for one specific offer -- backs "📄 Adapter mon CV pour
    cette offre" on the opportunity detail page.

    One row per candidate_match_id (unique), refreshed in place like
    CandidateMatch itself. Generated lazily: `POST
    /matching/{id}/cv-optimization` creates it on first request and simply
    returns the cached row on every later request for the same match --
    there's no "regenerate" control in the UI yet (see
    cv_optimization_service.py), so nothing here invalidates it if the
    candidate's CV or the match's own analysis changes afterwards. A known,
    documented limitation for this first version, not an oversight.

    `confirmed_at` is set by `POST /matching/{id}/cv-optimization/confirm`,
    the "Créer cette variante de CV" button -- today that's the entire
    effect of "confirming": it records that the candidate looked at this
    optimization and decided to keep it, so a later screen can show that
    intent. It does not (yet) produce a downloadable file or otherwise
    change the candidate's stored profile -- deliberately out of scope for
    this first version.
    """

    __tablename__ = "cv_optimizations"

    candidate_match_id: uuid.UUID = Field(index=True, unique=True, nullable=False)

    headline: str = Field(default="")
    summary: str = Field(default="")
    summary_why: str = Field(default="")
    # list[{title, company, period, bullets: [{text, status, original_text, why}]}]
    experiences: list = Field(default_factory=list, sa_column=Column(_JsonColumn))
    # list[{skill, added: bool}]
    skills: list = Field(default_factory=list, sa_column=Column(_JsonColumn))
    advice: str = Field(default="")

    computed_at: datetime = Field(sa_column=Column(DateTime(timezone=True)))
    confirmed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
