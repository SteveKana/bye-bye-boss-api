"""France Travail ("Offres d'emploi v2") provider.

Official, free API. Register an application at https://francetravail.io,
activate "Offres d'emploi v2", and set FRANCE_TRAVAIL_CLIENT_ID /
FRANCE_TRAVAIL_CLIENT_SECRET to enable this provider.

Auth is OAuth2 client_credentials: a short-lived bearer token is exchanged
for a search call. Ingestion runs infrequently (see
OFFERS_INGESTION_INTERVAL_MINUTES, default hourly), so a fresh token is
requested on every run rather than cached across runs -- simpler, and well
within the token endpoint's own rate limits.

Field names below (intitule, lieuTravail, typeContratLibelle, salaire,
entreprise, origineOffre...) follow the API's published response shape as
documented by the developer portal and third-party wrappers; every read is
defensive (`.get(...)`) so a field the API renames or omits degrades to
`None` on that one field instead of failing the whole run. Worth a quick
live sanity check (`python -m app.cli sync-offers`) once real credentials
are in place.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.offers.providers._util import parse_iso_datetime
from app.modules.offers.providers.base import NormalizedOffer, OfferProvider

logger = get_logger("offers.france_travail")

_TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    "?realm=%2Fpartenaire"
)
_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
_SCOPE = "api_offresdemploiv2 o2dsoffre"


class FranceTravailProvider(OfferProvider):
    source_name = "france_travail"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Accepting an injected client keeps this provider testable (see
        # tests/modules/offers) without touching the real network.
        self._client = client

    def is_configured(self) -> bool:
        settings = get_settings()
        return bool(
            settings.FRANCE_TRAVAIL_CLIENT_ID and settings.FRANCE_TRAVAIL_CLIENT_SECRET
        )

    async def _get_token(self, client: httpx.AsyncClient) -> str:
        settings = get_settings()
        response = await client.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.FRANCE_TRAVAIL_CLIENT_ID,
                "client_secret": settings.FRANCE_TRAVAIL_CLIENT_SECRET,
                "scope": _SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response.raise_for_status()
        return response.json()["access_token"]

    async def search(self, *, keywords: str, limit: int) -> list[NormalizedOffer]:
        if not self.is_configured():
            return []

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15)
        try:
            token = await self._get_token(client)
            response = await client.get(
                _SEARCH_URL,
                params={"motsCles": keywords, "range": f"0-{max(limit - 1, 0)}"},
                headers={"Authorization": f"Bearer {token}"},
            )
            # 200 = full result set, 206 = partial (range truncated by the
            # API) -- both are a successful search, not an error.
            if response.status_code not in (200, 206):
                response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("france_travail_search_failed", error=str(exc))
            return []
        finally:
            if owns_client:
                await client.aclose()

        return [self._normalize(item) for item in payload.get("resultats", [])]

    def _normalize(self, item: dict) -> NormalizedOffer:
        offer_id = str(item.get("id"))
        lieu = item.get("lieuTravail") or {}
        entreprise = item.get("entreprise") or {}
        salaire = item.get("salaire") or {}
        origine = item.get("origineOffre") or {}
        return NormalizedOffer(
            external_id=offer_id,
            title=item.get("intitule") or "",
            url=origine.get("urlOrigine")
            or f"https://candidat.francetravail.fr/offres/recherche/detail/{offer_id}",
            company_name=entreprise.get("nom"),
            description=item.get("description"),
            location=lieu.get("libelle"),
            contract_type=item.get("typeContratLibelle") or item.get("typeContrat"),
            salary_label=salaire.get("libelle"),
            published_at=parse_iso_datetime(item.get("dateCreation")),
            raw=item,
        )
