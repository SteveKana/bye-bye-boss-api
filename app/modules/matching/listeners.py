"""Matching-owned event listeners."""

from __future__ import annotations

from app.core.events import on
from app.modules.cv import ProfileOnboardingCompleted
from app.modules.matching import jobs as matching_jobs


@on(ProfileOnboardingCompleted)
async def run_matching_for_newly_onboarded_profile(
    event: ProfileOnboardingCompleted,
) -> None:
    """A candidate's first onboarding completion should surface real
    opportunities right away rather than waiting for the 18:00 sync -- see
    run_matching_for_new_profile's own docstring, and
    ProfileOnboardingCompleted's, for why this only ever fires once per
    account.

    Calls through the `matching_jobs` module object (not a direct
    `from ... import run_matching_for_new_profile`) so tests can
    monkeypatch `matching_jobs.run_matching_for_new_profile` -- a direct
    import would bind its own reference at load time, before any test
    fixture gets a chance to patch it."""
    await matching_jobs.run_matching_for_new_profile(event.profile_id)
