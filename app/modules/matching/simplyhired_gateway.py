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

---- Individual reviews (fetch_company_reviews, added 2026-10-02) ----------

Steve asked (2026-10-02) for the actual review text, after a screenshot of
Groupe SII's page proved wrong an earlier claim made in this project that no
free-text reviews exist on SimplyHired. That claim came from fetching the
page's plain HTML (no JS execution): the rendered page's individual reviews
turned out to be injected client-side, invisible to a plain GET -- a tooling
gap, not a fact about SimplyHired's data.

Re-verified (2026-10-02) via a JS-executing browser on the real Randstad and
Groupe-Sii pages: the reviews shown in the page are fetched by the browser,
after the initial page load, from SimplyHired's own JSON endpoint --
`{SIMPLYHIRED_BASE_URL}/api/next/company/reviews?companyname=<slug>&locale=
fr-FR` -- using the exact same slug as the HTML page URL. That endpoint
returns clean structured JSON (rating, job title, location, ISO date, title,
full untruncated text, pros, cons), so `fetch_company_reviews` below calls
it directly with httpx, the same way `fetch_company_ratings` calls the HTML
page -- no browser automation, no HTML parsing needed for this part.

IMPORTANT, verified the same day: this endpoint always returns the SAME
fixed batch of up to 10 reviews, however it's queried. Six common pagination
parameter names (page, pageNumber, offset, start, skip, reviewPage) were
each tried against the live Randstad page -- whose own displayed text says
"1 155 avis sur Indeed" -- and every single one came back with the
identical first review; the rendered page itself has no "load more"/"next"
control either. So despite SimplyHired citing a much larger Indeed-wide
review count, this endpoint (and therefore this scraper) can only ever see
this one fixed sample of up to 10 reviews per company -- not "all of them".
If SimplyHired ever adds real pagination here, this is the one function to
extend; there is currently no known way to get more than 10 through this
source.

Also note (same verification): the page states these reviews' ratings are
"sur Indeed" -- SimplyHired republishes Indeed-sourced review text here, not
its own original content. That sits less cleanly with this table's existing
scraping rationale above (republishing a *computed rating*, not raw
third-party text) -- flagged for Steve, not resolved unilaterally here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.dates import parse_iso_datetime
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


@dataclass
class SimplyHiredReview:
    """One individual review, as returned by the reviews JSON endpoint --
    see the module docstring's "Individual reviews" section for where this
    comes from and why there are at most 10 of these per company."""

    overall_rating: float | None
    job_title: str
    location: str
    review_date: datetime | None
    title: str
    text: str
    pros: str
    cons: str
    source_url: str = ""


def _browser_headers(settings) -> dict[str, str]:
    # A real browser never sends just a User-Agent -- a request with only
    # that one header, fired the instant the process starts, is itself a
    # bot signature many WAFs key on. This is still "identify honestly"
    # (see module docstring), not a CAPTCHA/fingerprint bypass: it's the
    # same header set Chrome sends on a normal navigation.
    return {
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

    try:
        async with httpx.AsyncClient(
            timeout=settings.SIMPLYHIRED_REQUEST_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                url,
                headers=_browser_headers(settings),
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


def _parse_reviews(payload: dict, url: str) -> list[SimplyHiredReview]:
    reviews = []
    for item in payload.get("reviewsContentGroup") or []:
        reviews.append(
            SimplyHiredReview(
                overall_rating=item.get("overallRating"),
                job_title=item.get("normalizedJobTitle") or "",
                location=item.get("normalizedLocation") or "",
                review_date=parse_iso_datetime(item.get("dateCreated")),
                title=item.get("title") or "",
                text=item.get("text") or "",
                pros=item.get("pros") or "",
                cons=item.get("cons") or "",
                source_url=url,
            )
        )
    return reviews


async def fetch_company_reviews(company_name: str) -> list[SimplyHiredReview] | None:
    """Best-effort fetch of `company_name`'s individual reviews -- see the
    module docstring's "Individual reviews" section for the source endpoint
    and why this is capped at ~10 reviews regardless of what's asked for.

    Returns None when nothing could be determined -- the slug can't be
    resolved, the request fails (network error, 403, 5xx, ...), or the
    payload doesn't parse -- and [] only for a confirmed, successful result
    with zero reviews (a 404, meaning SimplyHired itself says this company
    has no page). This split (CHANGED 2026-10-02) matters to
    regret_service.py's _refresh_reviews: before it existed, a transient
    failure and "genuinely zero reviews" were indistinguishable, both just
    [], so a SimplyHired outage (confirmed in production the same day --
    every company returning 403) would have wiped every previously-stored
    SimplyHired review on the very next refresh. Now a failed attempt
    returns None and the caller knows to leave existing rows alone, same
    "is this a genuine zero" distinction fetch_company_ratings already made
    by returning None on failure instead of some zero-value ratings
    object."""
    if not company_name or not company_name.strip():
        return None

    settings = get_settings()
    slug = _slugify(company_name)
    if not slug:
        return None
    url = f"{settings.SIMPLYHIRED_BASE_URL}/api/next/company/reviews"

    try:
        async with httpx.AsyncClient(
            timeout=settings.SIMPLYHIRED_REQUEST_TIMEOUT_SECONDS
        ) as client:
            response = await client.get(
                url,
                params={"companyname": slug, "locale": "fr-FR"},
                headers={**_browser_headers(settings), "Accept": "application/json"},
            )
            if response.status_code == 404:
                return []
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning(
            "simplyhired_reviews_fetch_failed", company=company_name, error=str(exc)
        )
        return None

    try:
        return _parse_reviews(payload, str(response.url))
    except Exception:
        logger.exception(
            "simplyhired_reviews_parse_failed", company=company_name, url=url
        )
        return None
