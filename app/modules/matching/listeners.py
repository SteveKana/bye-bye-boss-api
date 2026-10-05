"""Matching-owned event listeners."""

from __future__ import annotations

from sqlmodel import col, delete, select

from app.core.database import AsyncSessionLocal
from app.core.events import on
from app.modules.cv import CandidateProfileDeleting, ProfileOnboardingCompleted
from app.modules.matching import jobs as matching_jobs
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.models import CandidateMatch


@on(ProfileOnboardingCompleted)
async def run_matching_for_newly_onboarded_profile(
    event: ProfileOnboardingCompleted,
) -> None:
    """A candidate's first onboarding completion should surface real
    opportunities right away rather than waiting for the 08:00 sync -- see
    run_matching_for_new_profile's own docstring, and
    ProfileOnboardingCompleted's, for why this only ever fires once per
    account.

    Calls through the `matching_jobs` module object (not a direct
    `from ... import run_matching_for_new_profile`) so tests can
    monkeypatch `matching_jobs.run_matching_for_new_profile` -- a direct
    import would bind its own reference at load time, before any test
    fixture gets a chance to patch it."""
    await matching_jobs.run_matching_for_new_profile(event.profile_id)


@on(CandidateProfileDeleting)
async def purge_matches_of_deleted_profile(event: CandidateProfileDeleting) -> None:
    """Account deletion: remove this candidate's matches (and the CVs
    optimised for them). Pairs still waiting in an OpenAI batch are simply
    ignored when their result comes back -- the poll only applies results to
    rows that still exist."""
    async with AsyncSessionLocal() as session:
        match_ids = select(CandidateMatch.id).where(
            col(CandidateMatch.candidate_profile_id) == event.profile_id
        )
        await session.exec(
            delete(CVOptimization).where(
                col(CVOptimization.candidate_match_id).in_(match_ids)
            )
        )
        await session.exec(
            delete(CandidateMatch).where(
                col(CandidateMatch.candidate_profile_id) == event.profile_id
            )
        )
        await session.commit()
