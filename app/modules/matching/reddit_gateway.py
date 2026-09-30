"""Reddit read-only search client, used only by regret_service.py to gather
raw material for the Regret Index (see CompanyRegretProfile's docstring for
the full context on why this exists and what it doesn't settle).

Uses Reddit's own official, self-serve OAuth2 API (a free "script" app,
registered at reddit.com/prefs/apps) -- not raw HTML scraping. This is a
narrower risk than what the `offers` module explicitly refuses to do for
LinkedIn/Indeed/Glassdoor (there's no equivalent official read API for those
at all), but it is not risk-free: redistributing per-employer scores derived
from this data still falls under Reddit's Developer/Data API Terms, which
restrict some commercial uses. That tradeoff was accepted by Steve as a
product decision (2026-09-30), not resolved by using the official API
instead of scraping -- reusing the official endpoint just makes the result
far less fragile than parsing Reddit's rendered HTML would be.
"""

from __future__ import annotations

import time

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("matching.reddit")

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_SEARCH_URL = "https://oauth.reddit.com/search"

# Subreddits where French employees/candidates actually discuss employers --
# restricting to these (rather than a sitewide search) keeps results
# relevant and keeps the query volume Reddit sees from this app small.
_SUBREDDITS = ("france", "vosfinances", "developpeurs", "ChoisirSonPoste")


class _TokenCache:
    """A single app-level OAuth2 token, refreshed only once it's actually
    close to expiring -- client_credentials tokens are valid ~1h, and this
    job runs at most once a day per company (see REGRET_CACHE_TTL_DAYS), so
    there's no real concurrency to worry about here."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get(self, client: httpx.AsyncClient) -> str | None:
        settings = get_settings()
        if not (settings.REDDIT_CLIENT_ID and settings.REDDIT_CLIENT_SECRET):
            return None
        if self._token and time.monotonic() < self._expires_at:
            return self._token

        try:
            response = await client.post(
                _TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(settings.REDDIT_CLIENT_ID, settings.REDDIT_CLIENT_SECRET),
                headers={"User-Agent": settings.REDDIT_USER_AGENT},
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("reddit_token_failed", error=str(exc))
            return None

        self._token = payload.get("access_token")
        # Refresh 60s before actual expiry rather than cutting it exactly at
        # the wire -- avoids a request failing mid-flight over a few seconds
        # of clock drift.
        self._expires_at = time.monotonic() + max(payload.get("expires_in", 0) - 60, 0)
        return self._token


_token_cache = _TokenCache()


async def search_mentions(company_name: str, *, limit: int = 20) -> list[dict]:
    """Recent posts/comments across `_SUBREDDITS` mentioning `company_name`.
    Returns [] whenever the app isn't configured, the search errors, or
    nothing comes back -- callers (regret_service.py) treat an empty list
    exactly like "no signal", never differently from a hard failure, since
    neither case is a legitimate basis for a score.

    Each item is `{"title": str, "body": str, "permalink": str}` -- enough
    for the LLM prompt in regret_prompt.py, nothing else is kept."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        token = await _token_cache.get(client)
        if not token:
            return []

        query = f'"{company_name}" subreddit:({" OR ".join(_SUBREDDITS)})'
        try:
            response = await client.get(
                _SEARCH_URL,
                params={
                    "q": query,
                    "sort": "new",
                    "limit": limit,
                    "type": "link,comment",
                },
                headers={
                    "Authorization": f"Bearer {token}",
                    "User-Agent": settings.REDDIT_USER_AGENT,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("reddit_search_failed", company=company_name, error=str(exc))
            return []

        children = payload.get("data", {}).get("children", [])
        results = []
        for child in children:
            data = child.get("data", {})
            title = data.get("title") or ""
            body = data.get("body") or data.get("selftext") or ""
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
