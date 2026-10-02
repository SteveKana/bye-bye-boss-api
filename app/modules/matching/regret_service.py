"""Orchestrates the Regret Index for one company: cache lookup, SimplyHired
fetch, score conversion, cache write. Called from MatchingService._upsert
for every scored pair (see service.py), and from regret_jobs.py's monthly
bulk refresh -- kept as its own class so it gets its own repository/session
wiring, same convention as MatchingService itself.

Was Reddit + an LLM scoring step (see reddit_gateway.py/regret_gateway.py,
kept but no longer called); SimplyHired's rating is already a structured
number, so `_compute` below is a direct conversion, no LLM involved.

Never raises: any failure anywhere in the chain (no SimplyHired page found
for the company, request error, too few reviews) lands on
("unavailable", None), exactly like the "not enough signal" case -- callers
never need to distinguish "broken" from "no signal", since neither is a
legitimate basis for showing a candidate a number.
"""

from __future__ import annotations

from datetime import UTC, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import utcnow
from app.core.text import fold
from app.modules.matching import indeed_gateway, simplyhired_gateway
from app.modules.matching.models import CompanyRegretProfile, CompanyReview
from app.modules.matching.repository import (
    CompanyRegretRepository,
    CompanyReviewRepository,
)

logger = get_logger("matching.regret_service")


class RegretService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = CompanyRegretRepository(session)
        self.reviews_repo = CompanyReviewRepository(session)

    async def get_or_compute(
        self, company_name: str | None, *, force: bool = False
    ) -> tuple[str, int | None]:
        """Returns (regret_availability, regret_score) for `company_name`.
        Empty/missing company name -> unavailable, no fetch attempted.

        `force` skips the TTL check and always refetches -- used by
        regret_jobs.py's monthly bulk refresh, which is the primary refresh
        mechanism now (this TTL mostly matters for a company first
        encountered between two monthly runs, via the normal per-match call
        below)."""
        if not company_name or not company_name.strip():
            return "unavailable", None

        key = fold(company_name)
        settings = get_settings()
        existing = await self.repo.get_by_key(key)
        if existing is not None and not force:
            # SQLite drops tzinfo on round-trip even for a DateTime(timezone=
            # True) column (Postgres doesn't) -- normalize defensively so
            # this comparison works the same in tests and in production.
            computed_at = existing.computed_at
            if computed_at.tzinfo is None:
                computed_at = computed_at.replace(tzinfo=UTC)
            fresh_until = computed_at + timedelta(days=settings.REGRET_CACHE_TTL_DAYS)
            if utcnow() < fresh_until:
                return existing.regret_availability, existing.regret_score

        values = await self._compute(company_name)
        values["company_name"] = company_name
        values["computed_at"] = utcnow()

        if existing is None:
            await self.repo.create(CompanyRegretProfile(company_name_key=key, **values))
        else:
            await self.repo.update(existing, values)

        await self._refresh_reviews(key, company_name)

        return values["regret_availability"], values["regret_score"]

    async def _refresh_reviews(self, key: str, company_name: str) -> None:
        """Fetches and stores this company's individual reviews from both
        gateways, on the same schedule as the rating itself (whenever
        `_compute` above actually runs -- first lookup, TTL expiry, or a
        forced refresh). Fetched independently of whether the rating came
        back "available": Steve asked for the review text itself ("j'en
        veux un maximum", 2026-10-02), not just as a byproduct of a usable
        rating.

        Indeed (indeed_gateway) is a no-op returning [] until Steve sets up
        a Bright Data account -- see that module's docstring -- so this
        works exactly as before (SimplyHired's up-to-10 only) until then.

        Never raises: a failure in either gateway must not break the rating
        computation that triggered it, so each is caught and logged
        independently -- one source failing never takes the other down
        with it."""
        now = utcnow()
        rows: list[CompanyReview] = []

        try:
            simplyhired_reviews = await simplyhired_gateway.fetch_company_reviews(
                company_name
            )
        except Exception:
            logger.exception("simplyhired_reviews_refresh_failed", company=company_name)
            simplyhired_reviews = []

        try:
            indeed_reviews = await indeed_gateway.fetch_company_reviews(company_name)
        except Exception:
            logger.exception("indeed_reviews_refresh_failed", company=company_name)
            indeed_reviews = []

        for source_name, reviews in (
            ("simplyhired", simplyhired_reviews),
            ("indeed", indeed_reviews),
        ):
            for review in reviews:
                rows.append(
                    CompanyReview(
                        company_name_key=key,
                        source=source_name,
                        overall_rating=review.overall_rating,
                        job_title=review.job_title,
                        location=review.location,
                        review_date=review.review_date,
                        title=review.title,
                        text=review.text,
                        # IndeedReview has no pros/cons fields at all -- see
                        # indeed_gateway.py's docstring, no such split was
                        # found on the real page -- hence the getattr
                        # default rather than an attribute that doesn't
                        # exist on that dataclass.
                        pros=getattr(review, "pros", ""),
                        cons=getattr(review, "cons", ""),
                        source_url=review.source_url,
                        computed_at=now,
                    )
                )

        await self.reviews_repo.replace_for_company(key, rows)

    async def _compute(self, company_name: str) -> dict:
        settings = get_settings()
        ratings = await simplyhired_gateway.fetch_company_ratings(company_name)
        if ratings is None or ratings.review_count < settings.REGRET_MIN_REVIEWS:
            return {
                "regret_availability": "unavailable",
                "regret_score": None,
                "mention_count": ratings.review_count if ratings else 0,
                "overall_rating": ratings.overall_rating if ratings else None,
                "category_scores": ratings.category_scores if ratings else {},
                "satisfaction_percent": (
                    ratings.satisfaction_percent if ratings else None
                ),
                "source_url": ratings.source_url if ratings else "",
            }

        # SimplyHired's rating is "how good is this employer" (5 = best);
        # the Regret Index is "how likely is a candidate to regret joining"
        # (0 = best) -- a direct linear inversion, clamped for safety even
        # though a valid rating is always within [0, 5].
        score = round((5 - ratings.overall_rating) / 5 * 100)
        score = max(0, min(100, score))

        return {
            "regret_availability": "available",
            "regret_score": score,
            "mention_count": ratings.review_count,
            "overall_rating": ratings.overall_rating,
            "category_scores": ratings.category_scores,
            "satisfaction_percent": ratings.satisfaction_percent,
            "source_url": ratings.source_url,
        }
