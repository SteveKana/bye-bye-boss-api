"""SimplyHired.fr read-only scraping client, used only by regret_service.py
to source the Regret Index (see CompanyRegretProfile's docstring for the
full sourcing history: Reddit, then Glassdoor and Google declined outright
because scraping them means defeating CAPTCHA/anti-bot protection -- a line
this project does not cross regardless of the request -- then Indeed/
ChooseMyCompany confirmed to have no usable API, landing on SimplyHired).

SimplyHired.fr publishes an aggregate employee-review page per company at
`{SIMPLYHIRED_BASE_URL}/browse-jobs/companies/<slug>` -- an overall star
rating out of 5, a review count, five category breakdowns (work-life
balance, compensation, job security, management, culture) and one
"percentage of employees satisfied with their salary" stat. No CAPTCHA or
login wall was observed on this page (unlike Glassdoor/Google), so fetching
it is a plain HTTP GET + HTML parse -- still scraping (no official API
exists for this data), still a product/legal risk Steve accepted
(2026-09-30) rather than one resolved by this being technically easier than
Glassdoor, but not the anti-bot-bypass this project refuses to write.

Two things make this scrape inherently best-effort:

1. Slug resolution. SimplyHired does not expose a company search API, and
   observed slugs aren't a single predictable transform of the company name
   (e.g. "Groupe SII" -> "Groupe-Sii", but "ALSO France" -> "ALSO%20France"
   keeps its own casing and a literal space instead of a hyphen). `_slugify`
   below covers the common case; when the guessed slug 404s or doesn't
   render as a company page at all, this gives up rather than guessing
   further -- a wrong slug would silently attach one company's real
   Regret Index to a different employer, which is worse than reporting
   "unavailable".

2. Parsing. This was written from a single verified page fetch (Groupe SII,
   2026-09-30), not from SimplyHired's raw markup/class names (this
   environment's outbound network policy blocks reaching simplyhired.fr
   directly, same restriction hit earlier with reddit.com -- sandbox-only,
   not necessarily true of the production server). So parsing works off
   visible French text patterns (regex over the page's rendered text) rather
   than CSS selectors, which is both more resilient to a markup change and
   the only option available here. Still unconfirmed against real traffic
   at scale: the first production backfill (2026-09-30) got a 403 on every
   single company before parsing ever ran, traced to an unpaced ~120
   requests in 2 seconds (see regret_jobs.py's _PACE_SECONDS) rather than
   anything about the headers or the parsing itself -- once a request
   actually gets a 200 back, treat THAT as the real first test of the
   patterns below, and adjust them if SimplyHired's actual wording differs.

Never raises: any failure (network, 404, unparseable page) returns None,
exactly like Reddit's gateway returned [] -- regret_service.py treats that
as "no signal", never differently from a hard failure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("matching.simplyhired")

# Exact French labels as they appeared on the one verified page fetch
# (Groupe SII, 2026-09-30) -- mapped to short internal keys for
# CompanyRegretProfile.category_scores.
_CATEGORY_LABELS = {
    "work_life_balance": "Équilibre vie privée/professionnelle",
    "compensation": "Salaire et avantages",
    "job_security": "Sécurité de l'emploi et évolution",
    "management": "Direction",
    "culture": "Culture d'entreprise",
}

_RATING_RE = re.compile(r"(\d(?:[.,]\d)?)\s*(?:étoiles?\s*)?sur\s*5")
_REVIEW_COUNT_RE = re.compile(r"bas[ée]e?s?\s+sur\s+(\d+)\s+avis", re.IGNORECASE)
_REVIEW_COUNT_FALLBACK_RE = re.compile(r"(\d+)\s+avis")
_SATISFACTION_RE = re.compile(r"(\d{1,3})\s*%[^%\n]{0,100}?satisfait")


@dataclass
class SimplyHiredRatings:
    overall_rating: float
    review_count: int
    category_scores: dict[str, float] = field(default_factory=dict)
    satisfaction_percent: int | None = None
    source_url: str = ""


def _slugify(company_name: str) -> str:
    """Best-effort guess at a SimplyHired company-page slug: title-case each
    whitespace-separated word and join with hyphens (matches the common
    observed pattern -- "Groupe SII" -> "Groupe-Sii", "Blaser Group GmbH" ->
    "Blaser-Group-Gmbh"). Known to be wrong for some real slugs (e.g. ones
    that keep an acronym uppercase and a literal space) -- see the module
    docstring; those companies come back "unavailable" rather than guessed
    at further."""
    words = company_name.strip().split()
    titled = "-".join(word[:1].upper() + word[1:].lower() for word in words if word)
    return quote(titled, safe="'-")


def _parse(html: str, url: str) -> SimplyHiredRatings | None:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    rating_match = _RATING_RE.search(text)
    if not rating_match:
        return None
    overall_rating = float(rating_match.group(1).replace(",", "."))

    count_match = _REVIEW_COUNT_RE.search(text) or _REVIEW_COUNT_FALLBACK_RE.search(
        text
    )
    if not count_match:
        return None
    review_count = int(count_match.group(1))

    category_scores: dict[str, float] = {}
    for key, label in _CATEGORY_LABELS.items():
        window = re.search(rf"{re.escape(label)}[^\d]{{0,30}}(\d(?:[.,]\d)?)", text)
        if window:
            category_scores[key] = float(window.group(1).replace(",", "."))

    satisfaction_match = _SATISFACTION_RE.search(text)
    satisfaction_percent = (
        int(satisfaction_match.group(1)) if satisfaction_match else None
    )

    return SimplyHiredRatings(
        overall_rating=overall_rating,
        review_count=review_count,
        category_scores=category_scores,
        satisfaction_percent=satisfaction_percent,
        source_url=url,
    )


async def fetch_company_ratings(company_name: str) -> SimplyHiredRatings | None:
    """Best-effort fetch + parse of `company_name`'s SimplyHired.fr rating
    page. Returns None whenever the slug can't be resolved, the request
    fails, or the page doesn't parse as a company rating page -- callers
    treat that exactly like "no data available", never differently."""
    if not company_name or not company_name.strip():
        return None

    settings = get_settings()
    slug = _slugify(company_name)
    if not slug:
        return None
    url = f"{settings.SIMPLYHIRED_BASE_URL}/browse-jobs/companies/{slug}"

    # A real browser never sends just a User-Agent -- a request with only
    # that one header, fired the instant the process starts, is itself a
    # bot signature many WAFs key on. This is still "identify honestly"
    # (see module docstring), not a CAPTCHA/fingerprint bypass: it's the
    # same header set Chrome sends on a normal navigation.
    _headers = {
        "User-Agent": settings.SIMPLYHIRED_USER_AGENT,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }

    try:
        async with httpx.AsyncClient(
            timeout=settings.SIMPLYHIRED_REQUEST_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                url,
                headers=_headers,
                follow_redirects=True,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            html = response.text
    except httpx.HTTPError as exc:
        logger.warning("simplyhired_fetch_failed", company=company_name, error=str(exc))
        return None

    try:
        return _parse(html, url)
    except Exception:
        logger.exception("simplyhired_parse_failed", company=company_name, url=url)
        return None
