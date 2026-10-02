"""Indeed.fr individual review scraping, via Bright Data's Web Unlocker.

Added 2026-10-02, after Steve asked explicitly for more review coverage
than SimplyHired's own review endpoint can give: that endpoint always
returns the same fixed batch of up to 10 reviews per company, however it's
queried -- verified directly (see simplyhired_gateway.py's docstring), not
an assumption. SimplyHired itself cites a much larger review count as
coming from Indeed, so Indeed is the real source to go to for more.

A plain request to Indeed's review pages is blocked: fetching
`https://fr.indeed.com/cmp/<slug>/reviews` without an established browser
session returns a 403 immediately (verified 2026-10-02); with a real
browser session's cookies, the same URL returns 200 with the full review
text server-rendered in the HTML. That's the same kind of anti-bot gate
this project already refuses to defeat itself for Glassdoor/Google (see
simplyhired_gateway.py's docstring) -- so this module never tries to solve
that gate on its own. Bright Data's Web Unlocker
(https://brightdata.com/products/web-unlocker) is a paid third-party
service that has already built that bypass and sells access to it; this
project pays for access to already-public review content rather than
building an anti-bot bypass itself. Steve made this call explicitly
(2026-10-02) after being shown the alternative's cost.

Request shape -- CORRECTED 2026-10-02 against Steve's real account after the
first live run returned 400 Bad Request for every company:
`{"error":"Request validation failed", ..., "message":"\"format\" must be
one of [json, raw]"}`. The published docs this was first built from said
"html"; Bright Data's actual API only accepts "json" or "raw" -- "raw"
behaves the same way "html" was meant to (response body is the page's own
unblocked markup, no JSON envelope), so that's the fix, not a new format
handled differently:

    POST https://api.brightdata.com/request
    Authorization: Bearer <BRIGHTDATA_API_KEY>
    {"zone": "<BRIGHTDATA_WEB_UNLOCKER_ZONE>", "url": "<target>", "format": "raw"}

-> response body is the target page's own unblocked HTML, parsed below
exactly like SimplyHired's page (BeautifulSoup over real, verified
structure -- not CSS class names, which are build-hashed and unstable, but
`data-testid` attributes and schema.org/Review microdata, both confirmed
live on real Randstad/Groupe-Sii review pages, 2026-10-02):

  - one review per `[data-testid="reviews[]"]` block
  - rating: an `aria-label` reading "X,X/5 étoiles." inside that block
  - job title: the `<h4>` inside the `[itemprop="author"]` block
  - location: the one `<span>` inside that same author block (verified:
    there is exactly one, next to a map-pin icon) -- absent on some reviews
  - date: `[itemprop="datePublished"]`'s `content` attribute, a French
    long-form date ("7 septembre 2026") -- see core/dates.py's
    parse_french_date, the only format this page exposes (no ISO
    attribute anywhere on it)
  - title: `[data-testid="title"]`; body: `[data-testid="review-text"]`

No pros/cons split was found anywhere on the real page (unlike the subset
SimplyHired republishes, where some reviews do show one) -- CompanyReview's
pros/cons columns are simply left empty for Indeed-sourced rows.

Pagination: confirmed via the page's own "Suivant" link
(`/cmp/<slug>/reviews?start=20`) -- 20 reviews per page, one Bright Data
request per page. Capped by INDEED_REVIEWS_MAX_PAGES_PER_COMPANY (default
1 page = up to 20 reviews/company/refresh) to keep cost predictable --
Bright Data bills per request, not per review, so this is a cost control,
not a content limit like SimplyHired's hard 10-review ceiling.

Slug resolution reuses simplyhired_gateway's `_slugify`: verified the same
title-cased/hyphenated convention resolves real Indeed company pages too
(Randstad, Groupe-Sii both checked live) -- same best-effort caveat applies
(see that function's docstring).

Never raises, same convention as the rest of this module's siblings: any
failure (not configured, network, parse) returns [], never raises, so a
problem with Indeed/Bright Data never breaks the SimplyHired-based rating
or the 10-review fetch that already works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from app.core.config import get_settings
from app.core.dates import parse_french_date
from app.core.logging import get_logger
from app.modules.matching.simplyhired_gateway import _slugify

logger = get_logger("matching.indeed")

_REVIEWS_PER_PAGE = 20
_RATING_RE = re.compile(r"(\d[,.]\d)\s*/\s*5\s*étoiles?", re.IGNORECASE)


@dataclass
class IndeedReview:
    overall_rating: float | None
    job_title: str
    location: str
    review_date: datetime | None
    title: str
    text: str
    source_url: str = ""


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.BRIGHTDATA_API_KEY and settings.BRIGHTDATA_WEB_UNLOCKER_ZONE)


def _review_page_url(slug: str, *, start: int) -> str:
    base = f"https://fr.indeed.com/cmp/{slug}/reviews"
    return base if start == 0 else f"{base}?start={start}"


async def _fetch_unblocked_html(url: str) -> str | None:
    settings = get_settings()
    try:
        async with httpx.AsyncClient(
            timeout=settings.BRIGHTDATA_REQUEST_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                "https://api.brightdata.com/request",
                headers={
                    "Authorization": f"Bearer {settings.BRIGHTDATA_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "zone": settings.BRIGHTDATA_WEB_UNLOCKER_ZONE,
                    "url": url,
                    # "html" (from the published docs) is rejected by the
                    # real API -- "raw" is the corrected value, verified
                    # 2026-10-02 against Steve's account (see module
                    # docstring).
                    "format": "raw",
                },
            )
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as exc:
        # str(exc) alone is just the status line ("400 Bad Request for url
        # ...") -- Bright Data's actual reason (bad zone name, malformed
        # payload, etc.) is in the response body, so log that too, added
        # 2026-10-02 after a real first run returned 400 for every company
        # with no way to tell why from the logs alone.
        logger.warning(
            "brightdata_fetch_failed",
            url=url,
            error=str(exc),
            response_body=exc.response.text[:500],
        )
        return None
    except httpx.HTTPError as exc:
        logger.warning("brightdata_fetch_failed", url=url, error=str(exc))
        return None


def _parse_reviews_page(html: str, url: str) -> list[IndeedReview]:
    soup = BeautifulSoup(html, "html.parser")
    reviews: list[IndeedReview] = []

    for block in soup.select('[data-testid="reviews[]"]'):
        rating = None
        for el in block.select("[aria-label]"):
            label = el.get("aria-label")
            match = _RATING_RE.search(label) if isinstance(label, str) else None
            if match:
                rating = float(match.group(1).replace(",", "."))
                break

        job_title = ""
        location = ""
        author = block.select_one('[itemprop="author"]')
        if author is not None:
            h4 = author.find("h4")
            job_title = h4.get_text(strip=True) if h4 else ""
            # Verified live: exactly one visible <span> inside the author
            # block, next to a map-pin icon, holding the location -- absent
            # on reviews with no location set.
            for span in author.select("span"):
                text = span.get_text(strip=True)
                if text and text != job_title:
                    location = text
                    break

        date_meta = block.select_one('[itemprop="datePublished"]')
        review_date = None
        if date_meta is not None:
            content = date_meta.get("content")
            review_date = parse_french_date(
                content if isinstance(content, str) else None
            )

        title_el = block.select_one('[data-testid="title"]')
        text_el = block.select_one('[data-testid="review-text"]')

        reviews.append(
            IndeedReview(
                overall_rating=rating,
                job_title=job_title,
                location=location,
                review_date=review_date,
                title=title_el.get_text(strip=True) if title_el else "",
                text=text_el.get_text(strip=True) if text_el else "",
                source_url=url,
            )
        )

    return reviews


async def fetch_company_reviews(company_name: str) -> list[IndeedReview]:
    """Best-effort fetch of `company_name`'s Indeed reviews, up to
    INDEED_REVIEWS_MAX_PAGES_PER_COMPANY pages. Returns [] when not
    configured (no Bright Data account set up yet), the slug can't be
    resolved, or every page fetch/parse fails -- same "no signal" style as
    every sibling gateway in this module."""
    if not is_configured():
        return []
    if not company_name or not company_name.strip():
        return []

    slug = _slugify(company_name)
    if not slug:
        return []

    settings = get_settings()
    max_pages = max(1, settings.INDEED_REVIEWS_MAX_PAGES_PER_COMPANY)

    reviews: list[IndeedReview] = []
    for page_index in range(max_pages):
        url = _review_page_url(slug, start=page_index * _REVIEWS_PER_PAGE)
        html = await _fetch_unblocked_html(url)
        if html is None:
            break
        try:
            page_reviews = _parse_reviews_page(html, url)
        except Exception:
            logger.exception(
                "indeed_reviews_parse_failed", company=company_name, url=url
            )
            break
        if not page_reviews:
            break
        reviews.extend(page_reviews)

    return reviews
