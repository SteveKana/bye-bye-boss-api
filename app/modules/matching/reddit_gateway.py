"""Reddit read-only search client, used only by regret_service.py to gather
raw material for the Regret Index (see CompanyRegretProfile's docstring for
the full context on why this exists and what it doesn't settle).

UPDATE 2026-09-30 (same day as the original decision): Reddit locked down
self-serve OAuth2 app creation shortly after this module was first written
against it (their "Responsible Builder Policy" -- `reddit.com/prefs/apps`'s
"create app" button now just reloads the page without ever issuing
credentials; this is a known, widely reported change, not a bug on our
side). No client_id/client_secret is obtainable through the normal signup
flow any more, so this module was rewritten to hit Reddit's public,
unauthenticated search endpoint (the same JSON data Reddit's own website
fetches when you search on reddit.com, reachable by appending `.json` to a
reddit.com URL) instead of the OAuth API.

This is a step closer to the raw scraping the `offers` module already
refuses to do for LinkedIn/Indeed/Glassdoor than the original OAuth version
was -- there is no developer agreement covering this endpoint, and Reddit's
Terms of Use restrict automated access generally. Steve explicitly chose
this (2026-09-30, after being told Glassdoor's anti-bot/login wall makes it
an even worse target) over the alternative of doing nothing; nobody involved
in writing this is a lawyer, and that risk was accepted as a product
decision, not resolved by hitting a "public" URL instead of a documented
API. If that decision ever changes, this file (and regret_service.py's call
site in MatchingService._upsert) is the one place to revert.

Practical consequence of the switch: this endpoint only searches post
titles/selftext, not comments (comment search was only ever available
through the OAuth API) -- so coverage is narrower than the original version,
on top of being legally shakier. Still: [] on any failure/unconfigured
state, never raises -- same convention as every other provider gateway in
this codebase.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("matching.reddit")

_SEARCH_URL = "https://www.reddit.com/search.json"

# Subreddits where French employees/candidates actually discuss employers --
# restricting to these (rather than a sitewide search) keeps results
# relevant and keeps the request volume this sends to Reddit small.
_SUBREDDITS = ("france", "vosfinances", "developpeurs", "ChoisirSonPoste")


async def search_mentions(company_name: str, *, limit: int = 20) -> list[dict]:
    """Recent posts across `_SUBREDDITS` mentioning `company_name`, via
    Reddit's public search JSON (no auth). Returns [] whenever the request
    errors, is rate-limited/blocked, or nothing comes back -- callers
    (regret_service.py) treat an empty list exactly like "no signal", never
    differently from a hard failure, since neither case is a legitimate
    basis for a score.

    Each item is `{"title": str, "body": str, "permalink": str}` -- enough
    for the LLM prompt in regret_prompt.py, nothing else is kept."""
    settings = get_settings()
    query = f'"{company_name}" subreddit:({" OR ".join(_SUBREDDITS)})'

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(
                _SEARCH_URL,
                params={
                    "q": query,
                    "sort": "new",
                    "limit": limit,
                    "restrict_sr": "off",
                },
                headers={"User-Agent": settings.REDDIT_USER_AGENT},
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        # Covers rate-limiting (429) and outright blocks (403) the same way
        # as a network error -- there's nothing this caller can do about
        # either beyond trying again on the next scheduled run.
        logger.warning("reddit_search_failed", company=company_name, error=str(exc))
        return []

    children = payload.get("data", {}).get("children", [])
    results = []
    for child in children:
        data = child.get("data", {})
        title = data.get("title") or ""
        body = data.get("selftext") or ""
        if not (title or body):
            continue
        results.append(
            {
                "title": title,
                "body": body,
                "permalink": data.get("permalink", ""),
            }
        )
    return results
