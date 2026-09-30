"""Orchestrates the Regret Index for one company: cache lookup, Reddit
fetch, LLM scoring, cache write. Called from MatchingService._upsert for
every scored pair (see service.py) -- kept as its own class so it gets its
own repository/session wiring, same convention as MatchingService itself.

Never raises: any failure anywhere in the chain (no Reddit creds, Reddit
error, too few mentions, LLM failure, bad LLM output) lands on
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
from app.modules.matching import reddit_gateway, regret_gateway
from app.modules.matching.models import CompanyRegretProfile
from app.modules.matching.repository import CompanyRegretRepository

logger = get_logger("matching.regret_service")


class RegretService:
    def __init__(self, session: AsyncSession) -> None:
        self.repo = CompanyRegretRepository(session)

    async def get_or_compute(self, company_name: str | None) -> tuple[str, int | None]:
        """Returns (regret_availability, regret_score) for `company_name`.
        Empty/missing company name -> unavailable, no fetch attempted."""
        if not company_name or not company_name.strip():
            return "unavailable", None

        key = fold(company_name)
        settings = get_settings()
        existing = await self.repo.get_by_key(key)
        if existing is not None:
            # SQLite drops tzinfo on round-trip even for a DateTime(timezone=
            # True) column (Postgres doesn't) -- normalize defensively so
            # this comparison works the same in tests and in production.
            computed_at = existing.computed_at
            if computed_at.tzinfo is None:
                computed_at = computed_at.replace(tzinfo=UTC)
            fresh_until = computed_at + timedelta(days=settings.REGRET_CACHE_TTL_DAYS)
            if utcnow() < fresh_until:
                return existing.regret_availability, existing.regret_score

        availability, score, mention_count = await self._compute(company_name)

        values = {
            "company_name": company_name,
            "regret_availability": availability,
            "regret_score": score,
            "mention_count": mention_count,
            "computed_at": utcnow(),
        }
        if existing is None:
            await self.repo.create(CompanyRegretProfile(company_name_key=key, **values))
        else:
            await self.repo.update(existing, values)

        return availability, score

    async def _compute(self, company_name: str) -> tuple[str, int | None, int]:
        settings = get_settings()
        mentions = await reddit_gateway.search_mentions(company_name)
        if len(mentions) < settings.REGRET_MIN_MENTIONS:
            return "unavailable", None, len(mentions)

        analysis = await regret_gateway.analyse_regret(company_name, mentions)
        if analysis is None or analysis.availability.value == "insufficient":
            return "unavailable", None, len(mentions)

        return "available", analysis.score, len(mentions)
