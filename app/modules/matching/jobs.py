"""Scheduled matching.

2026-10-04 redesign (Steve): matching is a pipeline whose expensive stages
run through OpenAI's Batch API (-50% cost, results within 24h), so it is
driven by three jobs instead of one:

  * `sync_matches`, once a day at 18:00 Paris time: for every complete
    candidate, picks the offers ingested since the last run (see
    MatchingService), then submits what is waiting;
  * `submit_matching_batches`, every 15 minutes: submits whatever is waiting
    for a stage (also picks up brand-new candidates shortly after sign-up);
  * `poll_matching_batches`, every 10 minutes: applies finished batches
    (promotes/filters out after the pre-filter, stores full analyses) and
    triggers the notifications.

Also holds `run_matching_for_new_profile` -- an on-demand, one-off entry
point for a single profile, called by matching/listeners.py in reaction to
cv's `ProfileOnboardingCompleted` event (see its docstring below).
"""

from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.cv import CandidateProfileRepository
from app.modules.matching.batch_service import MatchingBatchService
from app.modules.matching.service import MatchingService
from app.modules.matching.skill_labels_service import SkillLabelService

logger = get_logger("matching.worker")


@scheduled(
    cron="0 18 * * *",  # 18:00, every day
    timezone="Europe/Paris",
    id="matching_sync",
)
async def sync_matches() -> None:
    async with AsyncSessionLocal() as session:
        await MatchingService(session).sync_all()
    await submit_matching_batches()


@scheduled(interval_minutes=15, id="matching_submit_batches")
async def submit_matching_batches() -> None:
    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).submit_pending()
    if report.batches_submitted:
        logger.info(
            "matching_batches_submitted",
            batches=report.batches_submitted,
            requests=report.requests_submitted,
        )


@scheduled(interval_minutes=10, id="matching_poll_batches")
async def poll_matching_batches() -> None:
    async with AsyncSessionLocal() as session:
        report = await MatchingBatchService(session).poll_batches()
    if report.batches_completed or report.batches_failed:
        logger.info(
            "matching_batches_polled",
            completed=report.batches_completed,
            failed_batches=report.batches_failed,
            promoted=report.promoted,
            filtered_out=report.filtered_out,
            scored=report.scored,
            failed_pairs=report.failed,
        )
    if report.scored:
        # Fresh analyses: get their skill names into French right away
        # rather than waiting for the 5-minute catch-up.
        await process_skill_labels()


async def process_skill_labels() -> int:
    """Catches the analysed matches up on the French skill-label glossary --
    see skill_labels_service.py. Never raises: a labelling problem must not
    take anything else down, the next run simply tries again."""
    settings = get_settings()
    try:
        async with AsyncSessionLocal() as session:
            done = await SkillLabelService(session).process_pending(
                limit=settings.MATCHING_LABELS_MATCHES_PER_RUN
            )
    except Exception:
        logger.exception("skill_labels_failed")
        return 0
    if done:
        logger.info("skill_labels_translated", matches=done)
    return done


@scheduled(interval_minutes=5, id="matching_skill_labels")
async def translate_skill_labels() -> None:
    await process_skill_labels()


async def run_matching_for_new_profile(profile_id: uuid.UUID) -> None:
    """One-off shortlisting for a single profile -- called by
    matching/listeners.py in reaction to cv's `ProfileOnboardingCompleted`
    event, itself only fired the very first time a profile completes
    onboarding (see that event's docstring for why this can't be
    re-triggered by re-uploading a CV or resaving preferences later). Lets
    a brand-new candidate see real offers on their dashboard right away
    (unscored, "analyse en cours") instead of waiting for the next 18:00
    sync -- the cheap pre-filter and the full analysis follow through the
    Batch API, and the "first opportunities ready" email goes out when they
    are done (see batch_service.py).

    That event is emitted from a FastAPI background task (see
    cv.routes.v1.cv_routes.update_preferences), started after the request's
    own response is sent -- the request's own DB session is already closed
    by then, so this opens its own, same as `sync_matches` above. Never
    raises: a failure here just means this candidate's dashboard stays empty
    until the next scheduled sync picks it up.
    """
    async with AsyncSessionLocal() as session:
        try:
            profile = await CandidateProfileRepository(session).get(profile_id)
            if profile is None:
                return
            report = await MatchingService(session).run_for_profile(profile)
            if report.profiles_skipped_no_cv_text or report.pairs_shortlisted:
                return
            # Nothing at all to evaluate (empty offer pool): no batch will ever
            # follow, so tell them now rather than leave them wondering
            # (Steve's explicit call, see emails.py).
            await MatchingBatchService(session).send_first_matches_email(
                profile_id, match_count=0
            )
            await session.commit()
        except Exception:
            logger.exception(
                "immediate_matching_for_new_profile_failed",
                profile_id=str(profile_id),
            )
