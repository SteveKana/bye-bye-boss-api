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


def _detail(match_id: uuid.UUID) -> str:
    return f"/api/v1/matching/{match_id}"


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


async def test_get_match_requires_auth(client: AsyncClient) -> None:
    r = await client.get(_detail(uuid.uuid4()))
    assert r.status_code == 401


async def test_get_match_404_when_no_profile(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    r = await client.get(_detail(uuid.uuid4()), headers=auth_headers)
    assert r.status_code == 404


async def test_get_match_404_when_match_does_not_exist(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    user_id = await _user_id("user@example.com")
    async with AsyncSessionLocal() as session:
        await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id, status=ProfileStatus.complete.value, raw_text="cv"
            )
        )
        await session.commit()

    r = await client.get(_detail(uuid.uuid4()), headers=auth_headers)
    assert r.status_code == 404


async def test_get_match_returns_full_analysis(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    user_id = await _user_id("user@example.com")

    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id, status=ProfileStatus.complete.value, raw_text="cv"
            )
        )
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id="1",
                title="Product Owner Data",
                description="Pilotage du backlog produit.",
                url="https://example.com/offre",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer.id,
                company_name="Astek",
                career_score=80,
                ats_score=60,
                ats_potential=75,
                blocking_message="Aucun frein majeur identifié.",
                analysis={"matches": [{"skill": "sql", "result": "matched"}]},
                computed_at=utcnow(),
            )
        )
        await session.commit()

    r = await client.get(_detail(match.id), headers=auth_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(match.id)
    assert body["company_name"] == "Astek"
    assert body["career_score"] == 80
    assert body["blocking_message"] == "Aucun frein majeur identifié."
    assert body["analysis"]["matches"][0]["skill"] == "sql"
    assert body["offer"]["title"] == "Product Owner Data"
    assert body["offer"]["url"] == "https://example.com/offre"


async def test_get_match_404_for_another_users_match(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    """Anti-IDOR: a match that exists but belongs to someone else returns
    404, same as one that doesn't exist at all -- never a 403 that would
    confirm its existence (see core/exceptions.py)."""
    owner_id = uuid.uuid4()  # a user unrelated to auth_headers
    caller_id = await _user_id("user@example.com")

    async with AsyncSessionLocal() as session:
        # The caller needs a completed profile of their own to get past the
        # get_for_user check before the ownership check is even reached.
        await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=caller_id, status=ProfileStatus.complete.value, raw_text="cv"
            )
        )
        someone_elses_profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=owner_id, status=ProfileStatus.complete.value, raw_text="cv"
            )
        )
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id="2",
                title="Offre d'un autre",
                url="https://example.com/autre",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=someone_elses_profile.id,
                job_offer_id=offer.id,
                career_score=50,
                ats_score=40,
                ats_potential=60,
                computed_at=utcnow(),
            )
        )
        await session.commit()

    r = await client.get(_detail(match.id), headers=auth_headers)
    assert r.status_code == 404
