"""Scheduled matching: scores complete profiles against relevant offers once
a day, so a candidate's dashboard always reads a pre-computed result instead
of waiting on an LLM call.

Was an interval (every MATCHING_INTERVAL_MINUTES) until the 2026-09-22 cost
review -- running once daily at a fixed local time, right after the day's
new offers have had a chance to come in, cuts LLM spend further without
losing much freshness for a still-small candidate base (see the config
history in app/core/config.py for the earlier interval-based step).

Also holds `run_matching_for_new_profile` -- an on-demand, one-off entry
point for a single profile, called by matching/listeners.py in reaction to
cv's `ProfileOnboardingCompleted` event (see both docstrings below). Not
part of the daily schedule itself, but living here because it opens its
own session the same way `sync_matches` does.
"""

from __future__ import annotations

import uuid

from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.auth import UserRepository
from app.modules.cv import CandidateProfileRepository
from app.modules.mailer import MailerGateway
from app.modules.matching.emails import build_first_matches_ready_email
from app.modules.matching.service import MatchingService

logger = get_logger("matching.worker")


@scheduled(
    cron="0 18 * * *",  # 18:00, every day
    timezone="Europe/Paris",
    id="matching_sync",
)
async def sync_matches() -> None:
    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).sync_all()
    if report.pairs_failed:
        logger.warning("matching_sync_had_failures", failed=report.pairs_failed)


async def run_matching_for_new_profile(profile_id: uuid.UUID) -> None:
    """One-off matching run for a single profile -- called by
    matching/listeners.py in reaction to cv's `ProfileOnboardingCompleted`
    event, itself only fired the very first time a profile completes
    onboarding (see that event's docstring for why this can't be
    re-triggered by re-uploading a CV or resaving preferences later). Lets
    a brand-new candidate see real opportunities on their dashboard right
    away instead of waiting for the next 18:00 sync.

    That event is emitted from a FastAPI background task (see
    cv.routes.v1.cv_routes.update_preferences), started after the request's
    own response is sent -- the request's own DB session is already closed
    by then, so this opens its own, same as `sync_matches` above. Never
    raises: a failure here just means this candidate's dashboard keeps
    showing "en cours d'analyse" until the next scheduled sync picks it up,
    rather than surfacing as a server error on onboarding completion.
    """
    async with AsyncSessionLocal() as session:
        try:
            profile = await CandidateProfileRepository(session).get(profile_id)
            if profile is None:
                return
            report = await MatchingService(session).run_for_profile(profile)
        except Exception:
            logger.exception(
                "immediate_matching_for_new_profile_failed",
                profile_id=str(profile_id),
            )
            return

        # Best-effort notification, in its own try/except so a mail problem
        # never makes this look like a failed matching run (the run itself
        # already succeeded and committed above). Always sent, even with
        # pairs_scored == 0 -- Steve's explicit call: a candidate who lands
        # on an empty dashboard should still hear that their analysis
        # finished, not silence. `pairs_scored` is a safe proxy for "matches
        # now visible on the dashboard" ONLY here, on this first-ever run for
        # a brand-new profile: nothing can already be fresh/skipped (see
        # `_run_for_profile`'s to_score logic), so every scored pair is a
        # newly created CandidateMatch row.
        try:
            user = await UserRepository(session).get(profile.user_id)
            if user is None:
                return
            mail = build_first_matches_ready_email(
                "fr", match_count=report.pairs_scored
            )
            await MailerGateway(session).enqueue(
                to_email=user.email,
                subject=mail.subject,
                text=mail.text,
                html=mail.html,
            )
            await session.commit()
        except Exception:
            logger.exception(
                "first_matches_ready_email_failed",
                profile_id=str(profile_id),
            )
