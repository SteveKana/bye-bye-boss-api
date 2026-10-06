"""Offers module — public surface.

Ingests job offers from official, legitimate sources (France Travail,
Adzuna) on a schedule, deduplicated by (source, external_id). Never scrapes
sites like LinkedIn/Indeed/Glassdoor/Welcome to the Jungle, whose Terms of
Service prohibit it (see providers/base.py). This is the raw material the
future `matching` module will score CVs against -- this module itself has
no opinion on matching, only on collecting and normalizing offers.
"""

from __future__ import annotations

from app.core.module import Module

# Import side effects: register the model (Alembic) and the ingestion job.
from app.modules.offers import jobs as jobs  # noqa: F401
from app.modules.offers import models as models  # noqa: F401
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository
from app.modules.offers.schemas import JobOfferRead
from app.modules.offers.service import OffersIngestionService

module = Module(
    name="offers",
    order=45,
    tags=["offers"],
)

__all__ = [
    "module",
    "JobOffer",
    "JobOfferRepository",
    "JobOfferRead",
    "OffersIngestionService",
]
