"""Stages 2 and 3 of the matching pipeline, run through OpenAI's Batch API
(-50% cost; results within 24h, see batch_gateway.py):

  shortlisted/placeholder --(cheap model, ATS score only)--> pending | filtered_out
  pending                 --(full analysis, gpt-5)---------> scored

Two scheduled jobs drive it (jobs.py): `submit_pending` packs every row
waiting for a stage into batches (never splitting one candidate's rows
across two batches, so the "keep the best N" rule below always sees all of
them), and `poll_batches` checks the in-flight batches and applies finished
ones. A row's request `custom_id` is its own id.

Stage 2 keeps, per candidate, the offers whose pre-filter ATS score is
>= MATCHING_PREFILTER_MIN_ATS -- at most MATCHING_MAX_OFFERS_PER_CANDIDATE of
them, best first; every other offer of that batch is `filtered_out` (kept as
a row so the pair is never evaluated, and billed, twice).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.events import event_bus
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.models import utcnow
from app.core.seniority import (
    candidate_years,
    career_score_penalty,
    required_years_range,
)
from app.modules.auth import UserRepository
from app.modules.cv import CandidateProfile, CandidateProfileRepository
from app.modules.mailer import MailerGateway
from app.modules.matching import batch_gateway, gateway
from app.modules.matching.emails import build_first_matches_ready_email
from app.modules.matching.events import MatchesScored
from app.modules.matching.models import CandidateMatch, MatchingBatch, MatchStatus
from app.modules.matching.prompt import build_prefilter_prompt, build_prompt
from app.modules.matching.repository import (
    CandidateMatchRepository,
    MatchingBatchRepository,
)
from app.modules.matching.service import _format_offer_text
from app.modules.offers import JobOfferRepository

logger = get_logger("matching.batch")

STAGE_PREFILTER = "prefilter"
STAGE_ANALYSIS = "analysis"


@dataclass
class SubmitReport:
    batches_submitted: int = 0
    requests_submitted: int = 0


@dataclass
class PollReport:
    batches_completed: int = 0
    batches_failed: int = 0
    promoted: int = 0
    filtered_out: int = 0
    scored: int = 0
    failed: int = 0
    scored_profile_ids: list[uuid.UUID] = field(default_factory=list)


class MatchingBatchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.matches = CandidateMatchRepository(session)
        self.batches = MatchingBatchRepository(session)
        self.profiles = CandidateProfileRepository(session)
        self.offers = JobOfferRepository(session)

    # ---- submit ---------------------------------------------------------
    async def submit_pending(self) -> SubmitReport:
        settings = get_settings()
        report = SubmitReport()
        for stage, statuses in (
            (
                STAGE_PREFILTER,
                (MatchStatus.shortlisted.value, MatchStatus.placeholder.value),
            ),
            (STAGE_ANALYSIS, (MatchStatus.pending.value,)),
        ):
            rows = await self.matches.list_unbatched(
                statuses, max_attempts=settings.MATCHING_MAX_ATTEMPTS
            )
            if rows:
                await self._submit_stage(stage, rows, report)
        return report

    async def _submit_stage(
        self, stage: str, rows: Sequence[CandidateMatch], report: SubmitReport
    ) -> None:
        settings = get_settings()
        by_profile: dict[uuid.UUID, list[CandidateMatch]] = defaultdict(list)
        for row in rows:
            by_profile[row.candidate_profile_id].append(row)

        # (rows, requests) per batch, never splitting a candidate's rows.
        chunks: list[tuple[list[CandidateMatch], list[dict]]] = []
        current_rows: list[CandidateMatch] = []
        current_requests: list[dict] = []
        for profile_id, profile_rows in by_profile.items():
            profile = await self.profiles.get(profile_id)
            built: list[tuple[CandidateMatch, dict]] = []
            for row in profile_rows:
                request = await self._build_request(stage, profile, row)
                if request is None:
                    # Profile without CV text / offer gone: nothing to ask.
                    await self.matches.update(
                        row, {"status": MatchStatus.filtered_out.value}
                    )
                    continue
                built.append((row, request))
            if not built:
                continue
            if (
                current_requests
                and len(current_requests) + len(built)
                > settings.MATCHING_BATCH_MAX_REQUESTS
            ):
                chunks.append((current_rows, current_requests))
                current_rows, current_requests = [], []
            for row, request in built:
                current_rows.append(row)
                current_requests.append(request)
        if current_requests:
            chunks.append((current_rows, current_requests))

        for chunk_rows, chunk_requests in chunks:
            try:
                openai_batch_id = await batch_gateway.submit_batch(chunk_requests)
            except AppError:
                # No API key / OpenAI hiccup: leave the rows as they are, the
                # next run of this job retries them.
                logger.warning("matching_batch_submit_skipped", stage=stage)
                continue
            batch = await self.batches.create(
                MatchingBatch(
                    openai_batch_id=openai_batch_id,
                    stage=stage,
                    request_count=len(chunk_requests),
                )
            )
            for row in chunk_rows:
                await self.matches.update(row, {"batch_id": batch.id})
            report.batches_submitted += 1
            report.requests_submitted += len(chunk_requests)
            logger.info(
                "matching_batch_submitted",
                stage=stage,
                requests=len(chunk_requests),
                openai_batch_id=openai_batch_id,
            )
        await self.session.commit()

    async def _build_request(
        self, stage: str, profile: CandidateProfile | None, row: CandidateMatch
    ) -> dict | None:
        settings = get_settings()
        offer = await self.offers.get(row.job_offer_id)
        if profile is None or not profile.raw_text or offer is None:
            return None
        offer_text = _format_offer_text(offer)
        if stage == STAGE_PREFILTER:
            return batch_gateway.build_request(
                custom_id=str(row.id),
                model=settings.MATCHING_PREFILTER_MODEL,
                prompt=build_prefilter_prompt(profile.raw_text, offer_text),
                effort="low",
                verbosity="low",
            )
        return batch_gateway.build_request(
            custom_id=str(row.id),
            model=settings.MATCHING_OPENAI_MODEL,
            prompt=build_prompt(profile.raw_text, offer_text),
            effort="medium",
            verbosity="medium",
        )

    # ---- poll -----------------------------------------------------------
    async def poll_batches(self) -> PollReport:
        report = PollReport()
        for batch in await self.batches.list_submitted():
            try:
                info = await batch_gateway.get_batch_info(batch.openai_batch_id)
            except AppError:
                continue  # transient; checked again on the next poll

            finished = info.status == "completed" or (
                info.status in batch_gateway.TERMINAL_FAILURE_STATUSES
            )
            if not finished:
                continue

            results: dict[str, str | None] = {}
            if info.output_file_id:
                try:
                    results = await batch_gateway.download_results(info.output_file_id)
                except AppError:
                    continue  # retry the download on the next poll
            rows = await self.matches.list_in_batch(batch.id)

            if batch.stage == STAGE_PREFILTER:
                await self._apply_prefilter(rows, results, report)
            else:
                await self._apply_analysis(rows, results, report)

            failed = info.status != "completed" and not results
            await self.batches.update(
                batch,
                {
                    "status": "failed" if failed else "completed",
                    "completed_at": utcnow(),
                },
            )
            if failed:
                report.batches_failed += 1
            else:
                report.batches_completed += 1
            await self.session.commit()

        if report.scored_profile_ids:
            await event_bus.emit(
                MatchesScored(profile_ids=list(report.scored_profile_ids))
            )
        return report

    def _register_failure(self, row: CandidateMatch) -> dict:
        """Values for a row whose result is missing/unusable: back to the
        queue for the next submission, or given up on after
        MATCHING_MAX_ATTEMPTS."""
        settings = get_settings()
        attempts = row.attempts + 1
        values: dict = {"batch_id": None, "attempts": attempts}
        if attempts >= settings.MATCHING_MAX_ATTEMPTS:
            values["status"] = MatchStatus.filtered_out.value
        return values

    async def _apply_prefilter(
        self,
        rows: Sequence[CandidateMatch],
        results: dict[str, str | None],
        report: PollReport,
    ) -> None:
        settings = get_settings()
        scored_rows: dict[uuid.UUID, list[CandidateMatch]] = defaultdict(list)
        had_placeholder: set[uuid.UUID] = set()
        retried_profiles: set[uuid.UUID] = set()
        for row in rows:
            if row.status == MatchStatus.placeholder.value:
                had_placeholder.add(row.candidate_profile_id)
            raw = results.get(str(row.id))
            score: int | None = None
            if raw is not None:
                try:
                    score = gateway.parse_prefilter_score(raw)
                except AppError:
                    score = None
            if score is None:
                await self.matches.update(row, self._register_failure(row))
                retried_profiles.add(row.candidate_profile_id)
                report.failed += 1
                continue
            await self.matches.update(row, {"prefilter_score": score})
            scored_rows[row.candidate_profile_id].append(row)

        for profile_id, profile_rows in scored_rows.items():
            ranked = sorted(
                profile_rows, key=lambda r: r.prefilter_score or 0, reverse=True
            )
            keep = [
                r
                for r in ranked
                if (r.prefilter_score or 0) >= settings.MATCHING_PREFILTER_MIN_ATS
            ][: settings.MATCHING_MAX_OFFERS_PER_CANDIDATE]
            keep_ids = {r.id for r in keep}
            for row in profile_rows:
                if row.id in keep_ids:
                    await self.matches.update(
                        row,
                        {"status": MatchStatus.pending.value, "batch_id": None},
                    )
                    report.promoted += 1
                else:
                    await self.matches.update(
                        row,
                        {"status": MatchStatus.filtered_out.value, "batch_id": None},
                    )
                    report.filtered_out += 1
            # A brand-new candidate whose first offers all failed the
            # pre-filter would otherwise hear nothing at all -- Steve's rule:
            # they are always told the analysis finished, even with zero
            # offers (see emails.py).
            if (
                not keep
                and profile_id in had_placeholder
                and profile_id not in retried_profiles
            ):
                await self.send_first_matches_email(profile_id, match_count=0)

    async def _apply_analysis(
        self,
        rows: Sequence[CandidateMatch],
        results: dict[str, str | None],
        report: PollReport,
    ) -> None:
        newly_scored: dict[uuid.UUID, int] = defaultdict(int)
        years_by_profile: dict[uuid.UUID, float | None] = {}
        for row in rows:
            raw = results.get(str(row.id))
            analysis = None
            if raw is not None:
                try:
                    analysis = gateway.parse_analysis(raw)
                except AppError:
                    analysis = None
            if analysis is None:
                await self.matches.update(row, self._register_failure(row))
                report.failed += 1
                continue
            career_score, seniority_penalty = await self._seniority_adjusted(
                row, analysis.career_score, years_by_profile
            )
            analysis_payload = analysis.model_dump(mode="json")
            if seniority_penalty:
                # The model's own score stays readable next to ours.
                analysis_payload["career_score_model"] = analysis.career_score
                analysis_payload["seniority_penalty"] = seniority_penalty
            await self.matches.update(
                row,
                {
                    "status": MatchStatus.scored.value,
                    "batch_id": None,
                    "company_name": analysis.company_name or row.company_name or "",
                    "career_score": career_score,
                    "ats_score": analysis.ats_score,
                    "ats_potential": analysis.ats_potential,
                    "blocking_message": analysis.blocking_message,
                    "analysis": analysis_payload,
                    "computed_at": utcnow(),
                },
            )
            newly_scored[row.candidate_profile_id] += 1
            report.scored += 1

        for profile_id, count in newly_scored.items():
            scored_before = await self._scored_count(profile_id) - count
            if scored_before <= 0:
                # First time this candidate has any analysed offer: the
                # "your first opportunities are ready" email, not the brief.
                await self.send_first_matches_email(profile_id, match_count=count)
            else:
                report.scored_profile_ids.append(profile_id)

    async def _seniority_adjusted(
        self,
        row: CandidateMatch,
        model_score: int,
        years_by_profile: dict[uuid.UUID, float | None],
    ) -> tuple[int, int]:
        """The Career Score after the seniority rule (Steve, 2026-10-05): lowered
        when the years the offer asks for are far from the candidate's, see
        core/seniority.py. Returns (score, points removed); an offer or profile
        that cannot be read leaves the model's score untouched."""
        profile_id = row.candidate_profile_id
        if profile_id not in years_by_profile:
            profile = await self.profiles.get(profile_id)
            years_by_profile[profile_id] = (
                candidate_years(profile.total_experience) if profile else None
            )
        years = years_by_profile[profile_id]
        if years is None:
            return model_score, 0
        offer = await self.offers.get(row.job_offer_id)
        if offer is None:
            return model_score, 0
        penalty = career_score_penalty(
            years, required_years_range(offer.title, offer.description)
        )
        if penalty <= 0:
            return model_score, 0
        return max(0, model_score - penalty), penalty

    async def _scored_count(self, profile_id: uuid.UUID) -> int:
        rows = await self.matches.list_all_for_profile(profile_id)
        return sum(1 for r in rows if r.status == MatchStatus.scored.value)

    async def send_first_matches_email(
        self, profile_id: uuid.UUID, *, match_count: int
    ) -> None:
        """Best-effort, in its own try/except: a mail problem must never
        undo or block the matching results being stored."""
        try:
            profile = await self.profiles.get(profile_id)
            if profile is None:
                return
            user = await UserRepository(self.session).get(profile.user_id)
            if user is None:
                return
            mail = build_first_matches_ready_email("fr", match_count=match_count)
            await MailerGateway(self.session).enqueue(
                to_email=user.email,
                subject=mail.subject,
                text=mail.text,
                html=mail.html,
            )
        except Exception:
            logger.exception(
                "first_matches_ready_email_failed", profile_id=str(profile_id)
            )
