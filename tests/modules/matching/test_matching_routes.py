from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository

TOP = "/api/v1/matching/top"


async def _user_id(email: str) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).first()
        return user.id


async def test_top_matches_requires_auth(client: AsyncClient) -> None:
    r = await client.get(TOP)
    assert r.status_code == 401


async def test_top_matches_404_when_no_profile(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get(TOP, headers=auth_headers)
    assert r.status_code == 404


async def test_top_matches_returns_precomputed_results_sorted_by_score(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    user_id = await _user_id("user@example.com")

    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id, status=ProfileStatus.complete.value, raw_text="cv"
            )
        )
        offers_repo = JobOfferRepository(session)
        offer_low = await offers_repo.create(
            JobOffer(
                source="test",
                external_id="low",
                title="Offre A",
                url="https://example.com/a",
            )
        )
        offer_high = await offers_repo.create(
            JobOffer(
                source="test",
                external_id="high",
                title="Offre B",
                url="https://example.com/b",
            )
        )
        matches_repo = CandidateMatchRepository(session)
        await matches_repo.create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer_low.id,
                career_score=40,
                ats_score=30,
                ats_potential=50,
                computed_at=utcnow(),
            )
        )
        await matches_repo.create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer_high.id,
                career_score=90,
                ats_score=80,
                ats_potential=95,
                computed_at=utcnow(),
            )
        )
        await session.commit()

    r = await client.get(TOP, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 2
    # Best career_score first.
    assert body[0]["career_score"] == 90
    assert body[0]["offer"]["title"] == "Offre B"
    assert body[1]["career_score"] == 40
    assert body[0]["regret_availability"] == "unavailable"
