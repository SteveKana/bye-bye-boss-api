from __future__ import annotations

import uuid

from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.mailer.models import EmailMessage
from app.modules.matching import jobs as matching_jobs
from app.modules.matching.models import MatchStatus
from app.modules.matching.repository import CandidateMatchRepository
from tests.modules.matching.test_service import _make_complete_profile, _make_offer


async def _make_user(**overrides) -> User:
    defaults = {"email": f"{uuid.uuid4()}@example.com", "is_verified": True}
    defaults.update(overrides)
    async with AsyncSessionLocal() as session:
        user = await UserRepository(session).create(User(**defaults))
        await session.commit()
        return user


async def _queued_emails(to_email: str) -> list[EmailMessage]:
    async with AsyncSessionLocal() as session:
        result = await session.exec(
            select(EmailMessage).where(EmailMessage.to_email == to_email)
        )
        return list(result.all())


async def test_run_matching_for_new_profile_shows_offers_without_email_yet() -> None:
    """The new candidate's closest offers are recorded straight away (visible
    placeholders, no score) -- the "first opportunities ready" email waits for
    the batch results instead of being sent here (see batch_service.py)."""
    user = await _make_user()
    profile = await _make_complete_profile(user_id=user.id)
    offer = await _make_offer()

    await matching_jobs.run_matching_for_new_profile(profile.id)

    async with AsyncSessionLocal() as session:
        match = await CandidateMatchRepository(session).get_by_profile_and_offer(
            profile.id, offer.id
        )
    assert match is not None
    assert match.status == MatchStatus.placeholder.value
    assert await _queued_emails(user.email) == []


async def test_run_matching_for_new_profile_sends_email_even_with_zero_matches() -> (
    None
):
    """Steve's explicit call: the candidate must still hear that their
    analysis finished, even when the very first run finds nothing to show
    on the dashboard -- never silence."""

    user = await _make_user()
    # No offers created at all -- shortlist_offers has nothing to select,
    # so the run completes with pairs_scored == 0.
    profile = await _make_complete_profile(user_id=user.id)

    await matching_jobs.run_matching_for_new_profile(profile.id)

    emails = await _queued_emails(user.email)
    assert len(emails) == 1
    assert emails[0].body_text  # zero-match copy still renders


async def test_run_matching_for_new_profile_skips_email_when_profile_missing() -> None:
    # Never happens in practice (the event fires right after the profile is
    # saved), but the listener must not blow up if it ever did.
    await matching_jobs.run_matching_for_new_profile(uuid.uuid4())
