"""Orchestrates the first stage of a matching run: for one candidate, pick the
offers worth evaluating and record them as `candidate_matches` rows waiting
for the next stages (cheap ATS pre-filter, then full analysis -- both run by
batch_service.py through OpenAI's Batch API, never from here).

Runs as a background job (see jobs.py) or on demand via
`python -m app.cli run-matching`; the dashboard never triggers this itself --
see the `matching` module's docstring.

2026-10-04 redesign (Steve): this used to score the 8 offers closest to the
CV with gpt-5 straight away. It now (1) takes the PREFILTER_POOL_SIZE (30)
closest *unseen* offers by embedding similarity, (2) records them as
`shortlisted` rows -- and, for a brand-new profile, flags its first
MATCHING_MAX_OFFERS_PER_CANDIDATE as visible `placeholder`s so the candidate
sees offers immediately, scores to follow --, (3) leaves everything else to
batch_service.py.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import embeddings
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import utcnow
from app.core.seniority import is_excluded_for_candidate
from app.modules.cv import CandidateProfile, CandidateProfileRepository, ProfileStatus
from app.modules.matching.models import (
    VISIBLE_STATUSES,
    ApplicationStatus,
    CandidateMatch,
    MatchStatus,
)

# RegretService import disabled 2026-10-03 (Steve: masquer/désactiver tout
# l'indice de regret, front et back) -- not removed, just never imported:
# re-enabling the feature later is uncommenting these spots, not rewriting
# them from scratch.
# from app.modules.matching.regret_service import RegretService
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.shortlist import shortlist_offers
from app.modules.offers import JobOffer, JobOfferRepository

logger = get_logger("matching.service")


@dataclass
class MatchingRunReport:
    profiles_processed: int = 0
    pairs_shortlisted: int = 0
    profiles_skipped_no_cv_text: int = 0
    # Offers kept away from a candidate by the seniority rules (Stage /
    # Alternance for an experienced profile, Junior from 3 years) -- each one
    # is a pre-filter call (and possibly a full analysis) not paid for.
    seniority_excluded: int = 0


def _as_utc(moment: datetime) -> datetime:
    """SQLite hands datetimes back naive (Postgres, in production, keeps the
    timezone) -- treat a naive one as UTC, which is what is stored."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _offer_date(offer: JobOffer) -> datetime:
    """When the offer went live: its publish date, or the day we ingested it
    when the source gave none."""
    return _as_utc(offer.published_at or offer.created_at)


def _format_offer_text(offer: JobOffer) -> str:
    """Reassemble an offer's structured fields into the kind of free-text
    block the prompt (ported from a raw-text prototype) expects."""
    lines = [offer.title]
    meta_fields = [offer.company_name, offer.location, offer.contract_type]
    meta = ", ".join(filter(None, meta_fields))
    if meta:
        lines.append(meta)
    if offer.salary_label:
        lines.append(offer.salary_label)
    elif offer.salary_min or offer.salary_max:
        salary_min = offer.salary_min or "?"
        salary_max = offer.salary_max or "?"
        lines.append(f"Salaire : {salary_min} - {salary_max} € / an")
    if offer.description:
        lines.append("")
        lines.append(offer.description)
    return "\n".join(lines)


class MatchingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.profiles = CandidateProfileRepository(session)
        self.offers = JobOfferRepository(session)
        self.matches = CandidateMatchRepository(session)
        # self.regret = RegretService(session)  # disabled 2026-10-03, see import above

    async def sync_all(self) -> MatchingRunReport:
        report = MatchingRunReport()
        profiles = await self.profiles.list(
            filters={"status": ProfileStatus.complete.value}
        )
        for profile in profiles:
            report.profiles_processed += 1
            await self._run_for_profile(profile, report)
        logger.info(
            "matching_sync_complete",
            profiles=report.profiles_processed,
            shortlisted=report.pairs_shortlisted,
            seniority_excluded=report.seniority_excluded,
        )
        return report

    async def run_for_profile(self, profile: CandidateProfile) -> MatchingRunReport:
        """Shortlist for a single profile -- called right after onboarding
        (jobs.run_matching_for_new_profile), and by the CLI/tests."""
        report = MatchingRunReport(profiles_processed=1)
        await self._run_for_profile(profile, report)
        return report

    async def _run_for_profile(
        self, profile: CandidateProfile, report: MatchingRunReport
    ) -> None:
        cv_text = profile.raw_text
        if not cv_text:
            # No extracted CV text to match against (shouldn't normally
            # happen for a "complete" profile, but never crash a whole run
            # over one odd profile).
            report.profiles_skipped_no_cv_text += 1
            return

        cv_changed = profile.embedding is None
        if cv_changed:
            # Lazily computed and cached rather than at CV-import time: this
            # also self-heals profiles that already existed before this
            # feature shipped, with no separate backfill migration/script
            # needed. Cleared back to None on every re-import (see
            # cv/service.import_cv) so a new CV is never scored against the
            # old one's embedding -- which is also how a new CV is detected
            # here (see `rescore_existing` below).
            embedding = await embeddings.get_embedding(cv_text)
            if embedding is not None:
                profile = await self.profiles.update(profile, {"embedding": embedding})

        settings = get_settings()
        now = utcnow()
        pool = await self.offers.list_recent(
            since=now - timedelta(days=settings.MATCHING_MAX_OFFER_POOL_DAYS)
        )
        # No geographic restriction: since 2026-10-05 (Steve) a candidate is
        # matched on their CV alone, and narrows by city/region themselves
        # with the filters on the Opportunités page. A mobility value saved
        # in an older profile is ignored.
        eligible = list(pool)

        # Seniority (Steve, 2026-10-05): an experienced candidate is never
        # offered a Stage/Alternance, nor a Junior post from 3 years of
        # experience. Decided here, before any LLM call. An excluded pair is
        # recorded as `filtered_out` (hidden, never evaluated or billed), and
        # a match already on file for one is hidden the same way -- unless the
        # candidate already acted on it.
        excluded_ids = {
            offer.id
            for offer in eligible
            if is_excluded_for_candidate(
                total_experience=profile.total_experience,
                experiences=profile.experiences,
                contract_type=offer.contract_type,
                title=offer.title,
            )
        }
        existing = {
            match.job_offer_id: match
            for match in await self.matches.list_all_for_profile(profile.id)
        }

        # First run = nothing on file yet for this profile; it looks at the
        # whole pool. A daily run only looks at offers ingested recently --
        # everything older was already seen on an earlier run.
        first_run = not existing
        if first_run:
            candidates = list(eligible)
        else:
            daily_since = now - timedelta(days=settings.MATCHING_DAILY_POOL_DAYS)
            candidates = [o for o in eligible if _as_utc(o.created_at) >= daily_since]

        # Freshness cap: never shortlist an offer published more than
        # MATCHING_MAX_OFFER_AGE_DAYS ago, whatever the run (Steve,
        # 2026-10-04). Offers already on file are left alone.
        oldest_allowed = now - timedelta(days=settings.MATCHING_MAX_OFFER_AGE_DAYS)
        candidates = [o for o in candidates if _offer_date(o) >= oldest_allowed]

        # An offer already seen for this candidate (whatever its outcome) is
        # never pre-filtered or analysed twice -- except after a CV change,
        # where the stored result no longer reflects the new CV: those rows
        # go back through the pipeline, keeping their application status.
        for offer_id in excluded_ids:
            stale = existing.get(offer_id)
            if (
                stale is not None
                and stale.status != MatchStatus.filtered_out.value
                and stale.application_status == ApplicationStatus.not_applied.value
            ):
                await self.matches.update(
                    stale, {"status": MatchStatus.filtered_out.value, "batch_id": None}
                )

        to_shortlist: list[JobOffer] = []
        for offer in candidates:
            if offer.id not in existing or cv_changed:
                if offer.id in excluded_ids:
                    await self._record(
                        profile.id,
                        offer,
                        MatchStatus.filtered_out,
                        existing.get(offer.id),
                    )
                    report.seniority_excluded += 1
                else:
                    to_shortlist.append(offer)
        shortlisted = shortlist_offers(
            profile, to_shortlist, limit=settings.MATCHING_PREFILTER_POOL_SIZE
        )

        for rank, offer in enumerate(shortlisted):
            previous = existing.get(offer.id)
            visible_before = (
                previous is not None and previous.status in VISIBLE_STATUSES
            )
            # Visible straight away (no score yet): a new profile's first
            # offers, and any offer the candidate could already see before a
            # CV change sent it back through the pipeline -- it must not
            # vanish from their list while it is being re-evaluated.
            status = (
                MatchStatus.placeholder
                if visible_before
                or (first_run and rank < settings.MATCHING_MAX_OFFERS_PER_CANDIDATE)
                else MatchStatus.shortlisted
            )
            await self._record(profile.id, offer, status, previous)
            report.pairs_shortlisted += 1

        await self.session.commit()

    async def _record(
        self,
        profile_id: uuid.UUID,
        offer: JobOffer,
        status: MatchStatus,
        existing: CandidateMatch | None,
    ) -> None:
        fresh_values = {
            "status": status.value,
            "prefilter_score": None,
            "batch_id": None,
            "attempts": 0,
        }
        if existing is not None:
            # Keeps its id (a CV optimisation / brief entry may point at it)
            # and its application status; only re-enters the pipeline.
            await self.matches.update(existing, fresh_values)
            return
        await self.matches.create(
            CandidateMatch(
                candidate_profile_id=profile_id,
                job_offer_id=offer.id,
                company_name=offer.company_name or "",
                career_score=0,
                ats_score=0,
                ats_potential=0,
                computed_at=utcnow(),
                **fresh_values,
            )
        )
