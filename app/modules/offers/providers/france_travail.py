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

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import httpx

from app.core.config import get_settings
from app.core.contract_type import guess_contract_type
from app.core.daily_rate import extract_daily_rate
from app.core.dates import parse_iso_datetime
from app.core.logging import get_logger
from app.core.regions import region_from_insee_code, region_from_postal_code
from app.core.remote_work import looks_full_remote
from app.core.salary import extract_annual_salary
from app.modules.offers.providers.base import NormalizedOffer, OfferProvider

logger = get_logger("offers.france_travail")

_TOKEN_URL = (
    "https://entreprise.francetravail.fr/connexion/oauth2/access_token"
    "?realm=%2Fpartenaire"
)
_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
_SCOPE = "api_offresdemploiv2 o2dsoffre"


# Metropolitan départements (Corsica as 2A/2B) and overseas ones, as the API's
# `departement` filter expects them.
DEPARTEMENTS: tuple[str, ...] = (
    *(f"{n:02d}" for n in range(1, 20)),
    "2A",
    "2B",
    *(f"{n:02d}" for n in range(21, 96)),
    "971",
    "972",
    "973",
    "974",
    "976",
)
# The API allows 10 calls/second; stay well under it.
_CALL_SPACING_SECONDS = 0.2
_PAGE_ATTEMPTS = 6
_RETRY_BACKOFF_SECONDS = 2.0
# One search returns at most 150 offers per call and 1,150 in total (range
# 0-1149): a window holding more is split (by département, then by time).
_PAGE_SIZE = 150
_MAX_PER_SEARCH = 1150
_MIN_SPLIT_SECONDS = 60


def _fmt(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _total_from_content_range(value: str | None) -> int | None:
    """`Content-Range: offres 0-0/12345` -> 12345."""
    if not value or "/" not in value:
        return None
    tail = value.rsplit("/", 1)[1].strip()
    return int(tail) if tail.isdigit() else None


class FranceTravailProvider(OfferProvider):
    source_name = "france_travail"

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        # Accepting an injected client keeps this provider testable (see
        # tests/modules/offers) without touching the real network.
        self._client = client
        # Slices of a bulk crawl that kept failing (see `_crawl_slice`); the
        # import is idempotent, so a re-run fills exactly these gaps.
        self.failed_slices: list[str] = []

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
        title = item.get("intitule") or ""
        description = item.get("description")
        # codePostal resolves a région directly and is preferred; commune is
        # an INSEE code (same département-prefix convention, see
        # core/regions.py) kept as a fallback for the rarer case where only
        # one of the two is present.
        region = region_from_postal_code(
            lieu.get("codePostal")
        ) or region_from_insee_code(lieu.get("commune"))
        salary_label = salaire.get("libelle")
        # A TJM shows up, when it's stated at all, either in the free-text
        # description or in this same salaire.libelle field France Travail
        # otherwise uses for a plain salary label -- see core/daily_rate.py.
        daily_rate_min, daily_rate_max = extract_daily_rate(description, salary_label)
        # France Travail never gives a structured salary figure, only this
        # label -- see core/salary.py. A permanent-role offer with no
        # plausible match here simply keeps salary_min/max unset (shown as
        # "salaire non précisé" on the frontend) rather than being guessed.
        salary_min, salary_max = extract_annual_salary(salary_label)
        # France Travail usually fills typeContratLibelle/typeContrat, but not
        # always -- same fallback as Adzuna's normalizer (see
        # core/contract_type.py) for the listings where neither is set, even
        # though the contract type is stated in plain text in the title or
        # description. Never overrides an actual provided value.
        contract_type = item.get("typeContratLibelle") or item.get("typeContrat")
        if not contract_type:
            contract_type = guess_contract_type(title, description)
        return NormalizedOffer(
            external_id=offer_id,
            title=title,
            url=origine.get("urlOrigine")
            or f"https://candidat.francetravail.fr/offres/recherche/detail/{offer_id}",
            company_name=entreprise.get("nom"),
            description=description,
            location=lieu.get("libelle"),
            contract_type=contract_type,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_label=salary_label,
            region=region,
            is_full_remote=looks_full_remote(title, description),
            daily_rate_min=daily_rate_min,
            daily_rate_max=daily_rate_max,
            # `dateActualisation` (last refreshed by the employer/agency) is
            # what France Travail's own site displays as "Actualisé le ..."
            # -- `dateCreation` is the offer's original creation date, which
            # for a long-running or re-surfaced offer can be far in the past
            # and made offers look stale (reported: an offer showing
            # "Actualisé le 18 septembre 2026" on France Travail was showing
            # as "publiée il y a 111 jours" here). Fall back to dateCreation
            # only if dateActualisation is absent.
            published_at=parse_iso_datetime(
                item.get("dateActualisation") or item.get("dateCreation")
            ),
            raw=item,
        )

    async def census(self, *, since: datetime) -> dict[str, int | None]:
        """How many offers France Travail holds that were created since
        `since`: one entry per département plus "ALL" (no département
        filter). Nothing is stored -- it only reads the total the API
        reports in `Content-Range`, so it sizes a bulk import before any is
        run. A value of None means the call failed for that département."""
        if not self.is_configured():
            return {}
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=20)
        counts: dict[str, int | None] = {}
        try:
            token = await self._get_token(client)
            for departement in ("ALL", *DEPARTEMENTS):
                params = {
                    "range": "0-0",
                    "minCreationDate": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "maxCreationDate": datetime.now(since.tzinfo).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                }
                if departement != "ALL":
                    params["departement"] = departement
                counts[departement] = await self._count(client, token, params)
                await asyncio.sleep(_CALL_SPACING_SECONDS)
        finally:
            if owns_client:
                await client.aclose()
        return counts

    async def _count(
        self, client: httpx.AsyncClient, token: str, params: dict[str, str]
    ) -> int | None:
        for _ in range(3):
            try:
                response = await client.get(
                    _SEARCH_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as exc:
                logger.warning("france_travail_census_failed", error=str(exc))
                return None
            if response.status_code == 204:
                return 0
            if response.status_code == 429:
                await asyncio.sleep(float(response.headers.get("Retry-After", "1")))
                continue
            if response.status_code not in (200, 206):
                logger.warning(
                    "france_travail_census_http_error", status=response.status_code
                )
                return None
            total = _total_from_content_range(response.headers.get("Content-Range"))
            if total is not None:
                return total
            return len(response.json().get("resultats", []))
        return None

    # ---- bulk crawl ---------------------------------------------------------

    async def _page(
        self,
        client: httpx.AsyncClient,
        auth: dict[str, str],
        params: dict[str, str],
    ) -> tuple[list[dict], int | None]:
        """One search call -> (offers, total reported by Content-Range).
        A 401 (token older than ~25 minutes) refreshes the token; a 429, a 5xx
        or a network error is retried with a growing pause; once the attempts
        are used up it raises (the crawl then records the slice as failed
        instead of stopping, see `_crawl_slice`)."""
        for attempt in range(_PAGE_ATTEMPTS):
            await asyncio.sleep(_CALL_SPACING_SECONDS)
            try:
                response = await client.get(
                    _SEARCH_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {auth['token']}"},
                )
            except httpx.TransportError as exc:
                logger.warning("france_travail_page_retry", error=str(exc))
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * 2**attempt)
                continue
            if response.status_code == 401:
                auth["token"] = await self._get_token(client)
                continue
            if response.status_code == 429:
                await asyncio.sleep(float(response.headers.get("Retry-After", "1")))
                continue
            if response.status_code >= 500:
                # Temporary server-side error (seen in production: a 500 on
                # one département page) -- wait and ask again.
                logger.warning("france_travail_page_retry", status=response.status_code)
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * 2**attempt)
                continue
            if response.status_code == 204:
                return [], 0
            if response.status_code not in (200, 206):
                response.raise_for_status()
            total = _total_from_content_range(response.headers.get("Content-Range"))
            return response.json().get("resultats", []), total
        raise httpx.HTTPError("France Travail: too many retries")

    async def crawl(
        self,
        *,
        since: datetime,
        until: datetime,
        departement: str | None = None,
    ) -> AsyncIterator[list[NormalizedOffer]]:
        """Every offer created in [since, until], tous métiers, in pages of
        up to 150. Starts nationally; a window holding more than the API's
        1,150-per-search cap is split by département, then by time, until
        each slice fits. Pages are yielded as they arrive so the caller can
        store them incrementally."""
        if not self.is_configured():
            return
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=30)
        try:
            auth = {"token": await self._get_token(client)}
            departements = [departement] if departement else [None]
            for dept in departements:
                async for page in self._crawl_slice(
                    client, auth, since, until, dept, may_split_by_dept=not departement
                ):
                    yield page
        finally:
            if owns_client:
                await client.aclose()

    async def _crawl_slice(
        self,
        client: httpx.AsyncClient,
        auth: dict[str, str],
        since: datetime,
        until: datetime,
        departement: str | None,
        *,
        may_split_by_dept: bool,
    ) -> AsyncIterator[list[NormalizedOffer]]:
        params = {"minCreationDate": _fmt(since), "maxCreationDate": _fmt(until)}
        if departement:
            params["departement"] = departement
        try:
            first, total = await self._page(client, auth, {**params, "range": "0-0"})
        except httpx.HTTPError as exc:
            self._record_failure(params, exc)
            return
        if not total:
            return
        if total > _MAX_PER_SEARCH:
            if may_split_by_dept and departement is None:
                for dept in DEPARTEMENTS:
                    async for page in self._crawl_slice(
                        client, auth, since, until, dept, may_split_by_dept=False
                    ):
                        yield page
                return
            if (until - since).total_seconds() > _MIN_SPLIT_SECONDS:
                middle = since + (until - since) / 2
                for lo, hi in ((since, middle), (middle + timedelta(seconds=1), until)):
                    async for page in self._crawl_slice(
                        client, auth, lo, hi, departement, may_split_by_dept=False
                    ):
                        yield page
                return
            logger.warning(
                "france_travail_slice_over_cap",
                total=total,
                departement=departement,
                since=_fmt(since),
            )
        last = min(total, _MAX_PER_SEARCH)
        for start in range(0, last, _PAGE_SIZE):
            end = min(start + _PAGE_SIZE, last) - 1
            try:
                items, _ = await self._page(
                    client, auth, {**params, "range": f"{start}-{end}"}
                )
            except httpx.HTTPError as exc:
                self._record_failure({**params, "range": f"{start}-{end}"}, exc)
                continue
            if items:
                yield [self._normalize(item) for item in items]

    def _record_failure(self, params: dict[str, str], exc: Exception) -> None:
        label = ", ".join(f"{key}={value}" for key, value in params.items())
        self.failed_slices.append(label)
        logger.error("france_travail_slice_failed", slice=label, error=str(exc))
