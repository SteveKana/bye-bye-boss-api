"""Job-offer source abstraction.

Ingestion needs job postings from *legitimate* sources. LinkedIn, Indeed,
Glassdoor and Welcome to the Jungle explicitly prohibit scraping in their
Terms of Service -- LinkedIn has real litigation history against scrapers
(hiQ Labs v. LinkedIn) -- so this project only integrates official,
self-serve/partner APIs: France Travail (official, free) and Adzuna
(official, free tier). The *source* is hidden behind this `OfferProvider`
interface so plugging in another legitimate source later is one new class;
nothing else in the codebase changes.

HOW TO PLUG ANOTHER SOURCE
    1. Write a class implementing `OfferProvider` (is_configured + search).
    2. Register it in providers/__init__.py::build_providers.
That's it -- OffersIngestionService and the scheduled job are unaware of
which concrete providers exist.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class NormalizedOffer:
    """A job offer normalized to a common shape, regardless of source."""

    external_id: str
    title: str
    url: str
    company_name: str | None = None
    description: str | None = None
    location: str | None = None
    contract_type: str | None = None
    remote_policy: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_label: str | None = None
    published_at: datetime | None = None
    # One of app.core.regions.FRENCH_REGIONS, or None when the source gave
    # us nothing (or nothing recognizable) to derive one from -- see each
    # provider's own _normalize for how it's worked out. Consumed by
    # matching/geo_filter.py: an offer with no région is excluded outright
    # whenever the candidate has restricted their search geographically.
    region: str | None = None
    # Best-effort keyword detection (see core/remote_work.py) -- a
    # fully-remote offer always bypasses the geographic filter regardless
    # of its région (or lack of one).
    is_full_remote: bool = False
    # Full original payload from the source, kept for later reprocessing.
    raw: dict = field(default_factory=dict)


class OfferProvider(abc.ABC):
    """Interface every job-offer source must implement.

    Contract:
      * `source_name` identifies the provider (stored on each `JobOffer` row
        and used to key deduplication together with the source's own id).
      * `is_configured` returns False when required credentials are missing
        -- ingestion then skips this provider rather than failing the run.
      * `search` raises only on genuine caller error; a request or network
        failure is caught internally and returns an empty list (logged as a
        warning) so one flaky provider never blocks the others.
    """

    source_name: str

    @abc.abstractmethod
    def is_configured(self) -> bool: ...

    @abc.abstractmethod
    async def search(self, *, keywords: str, limit: int) -> list[NormalizedOffer]: ...
