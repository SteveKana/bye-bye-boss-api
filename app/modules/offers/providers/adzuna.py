"""Adzuna provider.

Official, self-serve API (https://developer.adzuna.com) -- app_id/app_key in
the query string, no OAuth. Sign up there to get credentials and set
ADZUNA_APP_ID / ADZUNA_APP_KEY to enable this provider.

The free tier is limited (25 calls/min, 250/day, 1,000/week, 2,500/month at
the time this was written), so ingestion asks for a modest
`results_per_page` per keyword rather than paging deeply -- see
OFFERS_MAX_PER_KEYWORD.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.daily_rate import extract_daily_rate
from app.core.logging import get_logger
from app.core.regions import normalize_region_name
from app.core.remote_work import looks_full_remote
from app.modules.offers.providers._util import parse_iso_datetime
from app.modules.offers.providers.base import NormalizedOffer, OfferProvider

logger = get_logger("offers.adzuna")


class AdzunaProvider(OfferProvider):
    source_name = "adzuna"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    def is_configured(self) -> bool:
        settings = get_settings()
        return bool(settings.ADZUNA_APP_ID and settings.ADZUNA_APP_KEY)

    async def search(self, *, keywords: str, limit: int) -> list[NormalizedOffer]:
        if not self.is_configured():
            return []

        settings = get_settings()
        url = f"https://api.adzuna.com/v1/api/jobs/{settings.ADZUNA_COUNTRY}/search/1"

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15)
        try:
            response = await client.get(
                url,
                params={
                    "app_id": settings.ADZUNA_APP_ID,
                    "app_key": settings.ADZUNA_APP_KEY,
                    "results_per_page": limit,
                    "what": keywords,
                    "content-type": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("adzuna_search_failed", error=str(exc))
            return []
        finally:
            if owns_client:
                await client.aclose()

        return [self._normalize(item) for item in payload.get("results", [])]

    def _region_from_area(self, location: dict) -> str | None:
        """Adzuna's `location.area` is a hierarchical breadcrumb (e.g., for
        the UK, ["UK", "South East England", "Buckinghamshire", "Marlow"] --
        see https://developer.adzuna.com/docs/regional). No French example
        was available while building this, so rather than assume a fixed
        index for the région, every element is tried and the first one that
        normalizes to a known région (see core/regions.py) wins."""
        for element in location.get("area") or []:
            region = normalize_region_name(element)
            if region:
                return region
        return None

    def _normalize(self, item: dict) -> NormalizedOffer:
        company = item.get("company") or {}
        location = item.get("location") or {}
        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")
        title = item.get("title") or ""
        description = item.get("description")
        # Adzuna splits "permanent/contract" and "full_time/part_time" into
        # two separate fields; there's no single equivalent on our model, so
        # both are folded into the one free-text contract_type label.
        contract_type = ", ".join(
            v for v in (item.get("contract_type"), item.get("contract_time")) if v
        )
        # Adzuna has no free-text salary label like France Travail's
        # salaire.libelle -- title + description is all there is to look
        # for a stated TJM in (see core/daily_rate.py).
        daily_rate_min, daily_rate_max = extract_daily_rate(title, description)
        return NormalizedOffer(
            external_id=str(item.get("id")),
            title=title,
            url=item.get("redirect_url") or "",
            company_name=company.get("display_name"),
            description=description,
            location=location.get("display_name"),
            contract_type=contract_type or None,
            salary_min=int(salary_min) if salary_min is not None else None,
            salary_max=int(salary_max) if salary_max is not None else None,
            published_at=parse_iso_datetime(item.get("created")),
            region=self._region_from_area(location),
            is_full_remote=looks_full_remote(title, description),
            daily_rate_min=daily_rate_min,
            daily_rate_max=daily_rate_max,
            raw=item,
        )
