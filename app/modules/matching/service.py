"""Orchestrates a matching run: shortlist offers, score the new/changed
pairs with the LLM, upsert the results. Runs as a background job (see
jobs.py) or on demand via `python -m app.cli run-matching`; the dashboard
never triggers this itself -- see the `matching` module's docstring.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import embeddings
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.models import utcnow
from app.modules.cv import CandidateProfile, CandidateProfileRepository, ProfileStatus
from app.modules.matching import gateway
from app.modules.matching.geo_filter import filter_by_geography
from app.modules.matching.llm_schema import LLMAnalysis
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.shortlist import shortlist_offers
from app.modules.offers import JobOffer, JobOfferRepository

logger = get_logger("matching.service")


@dataclass
class MatchingRunReport:
    profiles_processed: int = 0
    pairs_scored: int = 0
    pairs_skipped_fresh: int = 0
    pairs_failed: int = 0
    profiles_skipped_no_cv_text: int = 0


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
            scored=report.pairs_scored,
            skipped_fresh=report.pairs_skipped_fresh,
            failed=report.pairs_failed,
        )
        return report

    async def run_for_profile(self, profile: CandidateProfile) -> MatchingRunReport:
        """Run matching for a single profile -- used by the CLI/tests, and
        reusable later for an on-demand "match me now" trigger if wanted."""
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

        if profile.embedding is None:
            # Lazily computed and cached rather than at CV-import time: this
            # also self-heals profiles that already existed before this
            # feature shipped, with no separate backfill migration/script
            # needed. Cleared back to None on every re-import (see
            # cv/service.import_cv) so a new CV is never scored against the
            # old one's embedding.
            embedding = await embeddings.get_embedding(cv_text)
            if embedding is not None:
                profile = await self.profiles.update(profile, {"embedding": embedding})

        settings = get_settings()
        since = utcnow() - timedelta(days=settings.MATCHING_MAX_OFFER_POOL_DAYS)
        pool = await self.offers.list_recent(since=since)
        # Enforce the candidate's mobility preference (see geo_filter's
        # docstring) before ranking -- an out-of-zone offer should never
        # occupy one of the limited shortlist slots in the first place.
        eligible = filter_by_geography(profile, pool)
        shortlisted = shortlist_offers(
            profile, eligible, limit=settings.MATCHING_MAX_OFFERS_PER_CANDIDATE
        )

        # DB reads stay sequential on the single session (AsyncSession isn't
        # safe for concurrent use) -- figure out up front which pairs are
        # already fresh and can be skipped without touching the LLM at all.
        to_score: list[tuple[JobOffer, CandidateMatch | None]] = []
        for offer in shortlisted:
            existing = await self.matches.get_by_profile_and_offer(profile.id, offer.id)
            if (
                existing is not None
                and existing.computed_at >= profile.updated_at
                and existing.computed_at >= offer.updated_at
            ):
                report.pairs_skipped_fresh += 1
                continue
            to_score.append((offer, existing))

        if not to_score:
            await self.session.commit()
            return

        # The LLM call is what's slow (network-bound, 1-2 minutes via OpenAI)
        # and touches no shared state, so score several offers at once --
        # bounded by MATCHING_CONCURRENCY to stay within OpenAI's rate limits.
        # DB writes (below) happen afterward, back on the single session.
        semaphore = asyncio.Semaphore(settings.MATCHING_CONCURRENCY)

        async def _score(offer: JobOffer) -> LLMAnalysis | AppError:
            async with semaphore:
                try:
                    return await gateway.analyse_match(
                        cv_text, _format_offer_text(offer)
                    )
                except AppError as exc:
                    return exc

        results = await asyncio.gather(*(_score(offer) for offer, _ in to_score))

        for (offer, existing), result in zip(to_score, results, strict=True):
            if isinstance(result, AppError):
                logger.warning(
                    "matching_pair_failed",
                    profile_id=str(profile.id),
                    offer_id=str(offer.id),
                    error=str(result),
                )
                report.pairs_failed += 1
                continue

            await self._upsert(
                profile.id, offer.id, offer.company_name, result, existing
            )
            report.pairs_scored += 1

        await self.session.commit()

    async def _upsert(
        self,
        profile_id: uuid.UUID,
        offer_id: uuid.UUID,
        offer_company_hint: str | None,
        analysis: LLMAnalysis,
        existing: CandidateMatch | None,
    ) -> None:
        values = {
            "company_name": analysis.company_name or offer_company_hint or "",
            "career_score": analysis.career_score,
            "ats_score": analysis.ats_score,
            "ats_potential": analysis.ats_potential,
            "blocking_message": analysis.blocking_message,
            "analysis": analysis.model_dump(mode="json"),
            # Always unavailable for now -- see the model's docstring.
            "regret_availability": "unavailable",
            "regret_score": None,
            "computed_at": utcnow(),
        }
        if existing is None:
            await self.matches.create(
                CandidateMatch(
                    candidate_profile_id=profile_id, job_offer_id=offer_id, **values
                )
            )
        else:
            await self.matches.update(existing, values)
