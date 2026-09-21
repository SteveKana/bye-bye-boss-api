from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field

from app.core.models import BaseModel


class OfferSource(enum.StrEnum):
    france_travail = "france_travail"
    adzuna = "adzuna"


# JSONB on Postgres (compact, indexable); plain JSON elsewhere (e.g. SQLite
# in tests) since JSONB has no SQLite equivalent -- same pattern as
# app/modules/cv/models.py.
_JsonColumn = JSON().with_variant(JSONB(), "postgresql")


class JobOffer(BaseModel, table=True):
    """A job offer ingested from an external source (France Travail, Adzuna...).

    Deduplicated per (source, external_id): re-running ingestion refreshes an
    already-known offer instead of duplicating it (see
    OffersIngestionService._upsert). `raw` keeps the full original payload so
    a later reprocessing -- e.g. building the future matching engine's LLM
    prompt -- never needs to re-fetch an offer that may since have been taken
    down at the source, and so a field we didn't think to normalize yet is
    never permanently lost.
    """

    __tablename__ = "job_offers"
    __table_args__ = (
        UniqueConstraint(
            "source", "external_id", name="uq_job_offers_source_external_id"
        ),
    )

    source: str = Field(index=True, nullable=False)
    # The id the source itself assigns to this offer -- unique only within
    # that source, hence the compound uniqueness with `source` above.
    external_id: str = Field(index=True, nullable=False)

    title: str = Field(nullable=False)
    company_name: str | None = Field(default=None)
    description: str | None = Field(default=None)
    location: str | None = Field(default=None)
    # Free-text label as given by the source (e.g. "CDI", "permanent,
    # full_time") -- sources don't share a common enum here, and normalizing
    # further isn't needed until the matching engine reads these fields.
    contract_type: str | None = Field(default=None)
    # Neither France Travail nor Adzuna exposes a reliable structured
    # remote/hybrid/on-site field -- left None for now rather than guessed
    # from free text; a future refinement could derive it from `description`.
    remote_policy: str | None = Field(default=None)
    # One of app.core.regions.FRENCH_REGIONS, derived at ingestion time from
    # whatever structured location data the source gives us (see each
    # provider's _normalize) -- None when that data was missing or
    # unrecognized. Consumed by matching/geo_filter.py to enforce a
    # candidate's "Région uniquement" mobility preference; an offer with no
    # région is excluded outright rather than assumed to match.
    region: str | None = Field(default=None, index=True)
    # Best-effort keyword detection over title+description (see
    # core/remote_work.py) -- a fully-remote offer always bypasses the
    # geographic filter in matching/geo_filter.py, regardless of `region`.
    is_full_remote: bool = Field(default=False, nullable=False)
    salary_min: int | None = Field(default=None)
    salary_max: int | None = Field(default=None)
    # Fallback for sources (France Travail) that only give a free-text
    # salary label instead of structured min/max figures.
    salary_label: str | None = Field(default=None)
    url: str = Field(nullable=False)
    published_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )

    raw: dict = Field(default_factory=dict, sa_column=Column(_JsonColumn))

    # Embedding vector of "title description" (see core/embeddings.py),
    # used by matching/shortlist.py to rank offers by semantic similarity to
    # a candidate's CV. Computed/refreshed at ingestion time -- see
    # OffersIngestionService._upsert_batch -- only when missing or when the
    # title/description actually changed, so an unchanged offer isn't
    # re-embedded on every hourly sync.
    embedding: list[float] | None = Field(default=None, sa_column=Column(_JsonColumn))
