"""Job-offer providers.

`build_providers()` returns every provider the app knows about; each one
decides for itself (via `is_configured`) whether it has credentials to run.
Ingestion simply skips an unconfigured provider (see
OffersIngestionService.sync), so adding France Travail or Adzuna credentials
later needs no code change -- just the .env values.
"""

from __future__ import annotations

from app.modules.offers.providers.adzuna import AdzunaProvider
from app.modules.offers.providers.base import NormalizedOffer, OfferProvider
from app.modules.offers.providers.france_travail import FranceTravailProvider

__all__ = [
    "NormalizedOffer",
    "OfferProvider",
    "AdzunaProvider",
    "FranceTravailProvider",
    "build_providers",
]


def build_providers() -> list[OfferProvider]:
    return [FranceTravailProvider(), AdzunaProvider()]
