"""Scheduled Regret Index refresh: once a month, (re)computes a SimplyHired-
based Regret Index for every company that appears anywhere in `job_offers`
(see offers/repository.py's list_distinct_company_names), not just the ones
a candidate is currently matched to.

This is the primary refresh mechanism for CompanyRegretProfile as of the
SimplyHired pivot (2026-09-30) -- Steve's explicit ask ("tu récupères toutes
les données possibles sur les entreprises et tu les stockes en base de
donnée", once a month). Before this job existed, a company's Regret Index
was only ever computed lazily, the first time a candidate matched to it
(see regret_service.py's get_or_compute, still called from
MatchingService._upsert for that reason -- a brand-new company shouldn't
have to wait for the next 1st-of-the-month run before it has any score at
all). This job forces a refresh regardless of REGRET_CACHE_TTL_DAYS
(`force=True`) so every known company gets scraped again every month even
if its existing row is technically still "fresh" by that TTL.

One SimplyHired request per company, sequentially -- deliberately not
parallelized, AND deliberately paced (see _PACE_SECONDS below). The first
real production run of this pivot (2026-09-30) hit every single company
with a 403: no artificial delay meant ~120 sequential-but-back-to-back
requests landed inside about 2 seconds, which reads exactly like a bot to
any rate-limiting on SimplyHired's side, independent of headers/UA. This
project already refuses to build anti-bot-bypass code for Glassdoor/
Google; a random pause between honestly-identified requests isn't that --
it's just not hammering the target, in the same spirit as staying
sequential (see simplyhired_gateway.py's docstring). A failure on one
company is logged and skipped -- never lets one bad company_name (or one
blocked/changed page) abort the run for the rest.
"""

from __future__ import annotations

import asyncio
import random

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.matching.regret_service import RegretService
from app.modules.offers import JobOfferRepository

logger = get_logger("matching.regret_jobs")

# Randomized pause between companies -- a monthly job has hours of slack,
# so there's no cost to spacing ~1-3s between requests, only upside for not
# looking like a scraper hammering the site.
_PACE_SECONDS = (1.5, 3.5)


@scheduled(
    cron="0 3 1 * *",  # 03:00, the 1st of every month
    timezone="Europe/Paris",
    id="regret_index_monthly_refresh",
)
async def refresh_all_company_regret_profiles() -> None:
    settings = get_settings()
    if not settings.REGRET_MONTHLY_REFRESH_ENABLED:
        return

    async with AsyncSessionLocal() as session:
        company_names = await JobOfferRepository(session).list_distinct_company_names()

    refreshed = 0
    failed = 0
    for index, company_name in enumerate(company_names):
        if index > 0:
            await asyncio.sleep(random.uniform(*_PACE_SECONDS))
        try:
            async with AsyncSessionLocal() as session:
                await RegretService(session).get_or_compute(company_name, force=True)
                await session.commit()
            refreshed += 1
        except Exception:
            failed += 1
            logger.exception(
                "regret_index_monthly_refresh_company_failed",
                company=company_name,
            )

    logger.info(
        "regret_index_monthly_refresh_done",
        total=len(company_names),
        refreshed=refreshed,
        failed=failed,
    )
