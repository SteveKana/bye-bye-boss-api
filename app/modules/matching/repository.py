from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlmodel import desc

from app.core.repository import BaseRepository
from app.modules.matching.models import (
    ApplicationStatus,
    CandidateMatch,
    CompanyRegretProfile,
    CompanyReview,
)


class CandidateMatchRepository(BaseRepository[CandidateMatch]):
    model = CandidateMatch

    async def get_by_profile_and_offer(
        self, candidate_profile_id: uuid.UUID, job_offer_id: uuid.UUID
    ) -> CandidateMatch | None:
        return await self.find_one(
            candidate_profile_id=candidate_profile_id, job_offer_id=job_offer_id
        )

    async def list_by_profile_and_offer_ids(
        self, candidate_profile_id: uuid.UUID, job_offer_ids: set[uuid.UUID]
    ) -> Sequence[CandidateMatch]:
        """Existing matches for this profile among a given set of offers --
        used by MatchingService to find matches that need pruning (see
        geo_filter.py) without one query per offer."""
        if not job_offer_ids:
            return []
        stmt = self._base_select().where(
            CandidateMatch.candidate_profile_id == candidate_profile_id,
            CandidateMatch.job_offer_id.in_(job_offer_ids),  # type: ignore[attr-defined]
        )
        return (await self.session.exec(stmt)).all()

    async def list_top_for_profile(
        self, candidate_profile_id: uuid.UUID, *, limit: int = 20
    ):
        # career_score and ats_potential are deliberately independent (see
        # matching/prompt.py's ETAPE 7/9 -- career_score ignores ATS filters
        # and blockers entirely, ats_potential already prices in a real
        # hard_blocker by staying low). Sorting on either alone lets the
        # other collapse to zero while still ranking near the top, so a
        # candidate can see a 91% match that's actually disqualified, or
        # vice versa. The product of the two only ranks a match highly when
        # BOTH are good -- a zero on either side sinks it (Steve's call).
        return await self.list(
            filters={"candidate_profile_id": candidate_profile_id},
            order_by=desc(CandidateMatch.career_score * CandidateMatch.ats_potential),
            limit=limit,
        )

    async def list_applications_for_profile(
        self, candidate_profile_id: uuid.UUID
    ) -> Sequence[CandidateMatch]:
        """Matches this candidate has a declared application status for --
        i.e. anything but the default `not_applied` -- backing the
        "Candidatures" page. `list()`'s `filters` only does equality, so
        this needs its own query, same as list_by_profile_and_offer_ids
        above. Most recently updated first."""
        stmt = (
            self._base_select()
            .where(
                CandidateMatch.candidate_profile_id == candidate_profile_id,
                CandidateMatch.application_status
                != ApplicationStatus.not_applied.value,
            )
            .order_by(desc(CandidateMatch.application_status_updated_at))
        )
        return (await self.session.exec(stmt)).all()


class CompanyRegretRepository(BaseRepository[CompanyRegretProfile]):
    model = CompanyRegretProfile

    async def get_by_key(self, company_name_key: str) -> CompanyRegretProfile | None:
        return await self.find_one(company_name_key=company_name_key)


class CompanyReviewRepository(BaseRepository[CompanyReview]):
    model = CompanyReview

    async def list_by_key(self, company_name_key: str) -> Sequence[CompanyReview]:
        return await self.list(filters={"company_name_key": company_name_key})

    async def replace_for_company_and_source(
        self, company_name_key: str, source: str, reviews: list[CompanyReview]
    ) -> None:
        """Deletes existing review rows for this company FROM THIS SOURCE
        ONLY, then inserts `reviews` in their place -- see CompanyReview's
        docstring for why a wholesale replace, not an upsert, is the right
        model (neither gateway hands back a stable external id to upsert
        against), and why it's scoped per source rather than across both
        gateways together (2026-10-02, replacing the former company-wide
        replace_for_company): _refresh_reviews in regret_service.py only
        calls this for a source it actually attempted to fetch this run, so
        skipping a source -- Bright Data left unconfigured on purpose, or a
        transient failure -- leaves that source's previously-stored rows
        exactly as they were instead of deleting them."""
        existing = await self.list(
            filters={"company_name_key": company_name_key, "source": source}
        )
        for row in existing:
            await self.delete(row)
        for review in reviews:
            await self.create(review)
