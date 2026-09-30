from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlmodel import desc, or_, select

from app.core.repository import BaseRepository
from app.modules.offers.models import JobOffer


class JobOfferRepository(BaseRepository[JobOffer]):
    model = JobOffer

    async def get_by_source_external_id(
        self, source: str, external_id: str
    ) -> JobOffer | None:
        return await self.find_one(source=source, external_id=external_id)

    async def list_missing_contract_type(self) -> Sequence[JobOffer]:
        """Offers with no contract_type at all -- backs the one-off
        `backfill-contract-type` CLI command that applies core/contract_type's
        keyword guess to offers ingested before either provider had that
        fallback wired in (see adzuna.py/france_travail.py's `_normalize`).
        `filters` on `list()` only does equality, so this needs its own
        query for "is null or empty string"."""
        stmt = self._base_select().where(
            or_(JobOffer.contract_type.is_(None), JobOffer.contract_type == "")  # type: ignore[attr-defined]
        )
        return (await self.session.exec(stmt)).all()

    async def list_recent(self, *, since: datetime) -> Sequence[JobOffer]:
        """Offers ingested on or after `since`, most recently published
        first -- used by the matching module to bound the pool of offers it
        even considers shortlisting (see MATCHING_MAX_OFFER_POOL_DAYS)."""
        stmt = (
            self._base_select()
            .where(JobOffer.created_at >= since)
            .order_by(desc(JobOffer.published_at))
        )
        return (await self.session.exec(stmt)).all()

    async def list_distinct_company_names(self) -> Sequence[str]:
        """Every distinct non-blank employer name across all known offers --
        backs regret_jobs.py's monthly Regret Index bulk refresh, which
        needs "every company we've ever seen", not just the ones a candidate
        currently matches (this table, not candidate_matches, is the
        complete set: an employer stays known here even once its offers age
        out of MATCHING_MAX_OFFER_POOL_DAYS)."""
        stmt = (
            select(JobOffer.company_name)
            .where(
                JobOffer.company_name.is_not(None),  # type: ignore[attr-defined]
                JobOffer.company_name != "",
            )
            .distinct()
        )
        if self._soft_delete:
            stmt = stmt.where(JobOffer.deleted_at.is_(None))  # type: ignore[attr-defined]
        return (await self.session.exec(stmt)).all()
