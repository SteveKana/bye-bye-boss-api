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
parallelized. This project already refuses to build anti-bot-bypass code
for Glassdoor/Google; hammering SimplyHired with concurrent requests once a
month would cut against the same spirit even though SimplyHired itself
isn't declined outright (see simplyhired_gateway.py's docstring). A failure
on one company is logged and skipped -- never lets one bad company_name
(or one blocked/changed page) abort the run for the rest.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.matching.regret_service import RegretService
from app.modules.offers import JobOfferRepository

logger = get_logger("matching.regret_jobs")


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
    for company_name in company_names:
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
