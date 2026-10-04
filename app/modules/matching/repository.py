from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_
from sqlmodel import col, desc

from app.core.repository import BaseRepository
from app.modules.matching.models import (
    VISIBLE_STATUSES,
    ApplicationStatus,
    CandidateMatch,
    CompanyRegretProfile,
    CompanyReview,
    MatchingBatch,
    MatchStatus,
    SkillLabel,
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

    async def list_all_for_profile(
        self, candidate_profile_id: uuid.UUID
    ) -> Sequence[CandidateMatch]:
        """Every row this profile has, whatever its status -- the matching
        run needs the full set to know which offers were already seen."""
        return await self.list(filters={"candidate_profile_id": candidate_profile_id})

    async def list_visible_for_profile(
        self, candidate_profile_id: uuid.UUID, *, limit: int
    ) -> Sequence[CandidateMatch]:
        """The /opportunites history: the `limit` most recently created
        matches the candidate can see (placeholder/pending/scored)."""
        stmt = (
            self._base_select()
            .where(
                CandidateMatch.candidate_profile_id == candidate_profile_id,
                CandidateMatch.status.in_(VISIBLE_STATUSES),  # type: ignore[attr-defined]
            )
            .order_by(desc(CandidateMatch.created_at))
            .limit(limit)
        )
        return (await self.session.exec(stmt)).all()

    async def list_dashboard_candidates(
        self,
        candidate_profile_id: uuid.UUID,
        *,
        shown_since: datetime,
        min_ats: int,
    ) -> Sequence[CandidateMatch]:
        """Matches eligible for the dashboard: visible, not applied to, never
        shown on it yet or first shown within the window (`shown_since`),
        and -- once fully analysed -- with a final ATS score >= `min_ats`
        (a not-yet-analysed one already passed the pre-filter at that
        threshold). Ranking/limiting is left to the caller."""
        stmt = self._base_select().where(
            CandidateMatch.candidate_profile_id == candidate_profile_id,
            CandidateMatch.status.in_(VISIBLE_STATUSES),  # type: ignore[attr-defined]
            CandidateMatch.application_status == ApplicationStatus.not_applied.value,
            or_(
                col(CandidateMatch.dashboard_first_shown_at).is_(None),
                col(CandidateMatch.dashboard_first_shown_at) >= shown_since,
            ),
            or_(
                CandidateMatch.status != MatchStatus.scored.value,
                CandidateMatch.ats_score >= min_ats,
            ),
        )
        return (await self.session.exec(stmt)).all()

    async def list_awaiting_labels(self, *, limit: int) -> Sequence[CandidateMatch]:
        """Analysed matches whose skill names have not been through the
        French glossary yet (newest first -- what candidates look at)."""
        stmt = (
            self._base_select()
            .where(
                CandidateMatch.status == MatchStatus.scored.value,
                col(CandidateMatch.labels_done).is_(False),
            )
            .order_by(desc(CandidateMatch.computed_at))
            .limit(limit)
        )
        return (await self.session.exec(stmt)).all()

    async def list_unbatched(
        self, statuses: Sequence[str], *, max_attempts: int
    ) -> Sequence[CandidateMatch]:
        """Rows waiting for their next request to be submitted (not in a
        batch, retries not exhausted)."""
        stmt = self._base_select().where(
            CandidateMatch.status.in_(list(statuses)),  # type: ignore[attr-defined]
            col(CandidateMatch.batch_id).is_(None),
            CandidateMatch.attempts < max_attempts,
        )
        return (await self.session.exec(stmt)).all()

    async def list_in_batch(self, batch_id: uuid.UUID) -> Sequence[CandidateMatch]:
        return await self.list(filters={"batch_id": batch_id})

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
        # Fully analysed matches only -- the others have no score yet.
        return await self.list(
            filters={
                "candidate_profile_id": candidate_profile_id,
                "status": MatchStatus.scored.value,
            },
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


class MatchingBatchRepository(BaseRepository[MatchingBatch]):
    model = MatchingBatch

    async def list_submitted(self) -> Sequence[MatchingBatch]:
        return await self.list(filters={"status": "submitted"})


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


class SkillLabelRepository(BaseRepository[SkillLabel]):
    model = SkillLabel

    async def labels_for(self, keys: Sequence[str]) -> dict[str, str]:
        """key -> French label, for the keys the glossary already knows."""
        found: dict[str, str] = {}
        wanted = list(keys)
        # Chunked: stays well under every database's bound-parameter limit.
        for i in range(0, len(wanted), 500):
            stmt = self._base_select().where(
                col(SkillLabel.key).in_(wanted[i : i + 500])
            )
            for row in (await self.session.exec(stmt)).all():
                found[row.key] = row.label
        return found
