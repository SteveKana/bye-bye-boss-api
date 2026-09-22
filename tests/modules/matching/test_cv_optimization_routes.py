from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlmodel import select

from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.auth.models import User
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching import cv_optimization_gateway
from app.modules.matching.cv_optimization_schema import CVOptimizationResult
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository


def _generate_url(match_id: uuid.UUID) -> str:
    return f"/api/v1/matching/{match_id}/cv-optimization"


def _confirm_url(match_id: uuid.UUID) -> str:
    return f"/api/v1/matching/{match_id}/cv-optimization/confirm"


async def _user_id(email: str) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        user = (await session.exec(select(User).where(User.email == email))).first()
        return user.id


async def _profile_and_match(email: str = "user@example.com") -> CandidateMatch:
    user_id = await _user_id(email)
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(
                user_id=user_id,
                status=ProfileStatus.complete.value,
                raw_text="cv",
                experiences=[
                    {
                        "title": "Product Owner",
                        "company": "Doctolib",
                        "period": "2022-2026",
                        "description": "Gestion du backlog produit",
                    }
                ],
                skills=["Agile"],
            )
        )
        offer = await JobOfferRepository(session).create(
            JobOffer(
                source="test",
                external_id="cv-opt-route",
                title="Product Owner Data",
                url="https://example.com/offre-cv-opt",
            )
        )
        match = await CandidateMatchRepository(session).create(
            CandidateMatch(
                candidate_profile_id=profile.id,
                job_offer_id=offer.id,
                career_score=70,
                ats_score=60,
                ats_potential=85,
                analysis={"job_skills": [{"skill": "sql"}]},
                computed_at=utcnow(),
            )
        )
        await session.commit()
    return match


def _mock_gateway(monkeypatch, *, calls: list | None = None):
    async def _fake_optimize_cv(cv, offer_text, analysis, **kwargs):
        if calls is not None:
            calls.append((cv, offer_text, analysis))
        return CVOptimizationResult(
            headline="Product Owner orienté Data",
            summary="Résumé optimisé",
            summary_why="Pertinent pour cette offre.",
            advice="Conseil concret.",
        )

    monkeypatch.setattr(cv_optimization_gateway, "optimize_cv", _fake_optimize_cv)


async def test_generate_cv_optimization_requires_auth(client: AsyncClient) -> None:
    r = await client.post(_generate_url(uuid.uuid4()))
    assert r.status_code == 401


async def test_generate_cv_optimization_creates_and_returns_result(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _mock_gateway(monkeypatch)
    match = await _profile_and_match()

    r = await client.post(_generate_url(match.id), headers=auth_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["headline"] == "Product Owner orienté Data"
    assert body["summary_why"] == "Pertinent pour cette offre."
    assert body["confirmed_at"] is None
    # Reused straight from the match, never invented by this call.
    assert body["ats_score_before"] == 60
    assert body["ats_score_after"] == 85


async def test_generate_cv_optimization_is_cached_on_second_call(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    calls: list = []
    _mock_gateway(monkeypatch, calls=calls)
    match = await _profile_and_match()

    first = await client.post(_generate_url(match.id), headers=auth_headers)
    second = await client.post(_generate_url(match.id), headers=auth_headers)

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(calls) == 1  # the LLM is only ever called once per match


async def test_generate_cv_optimization_404_for_another_users_match(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _mock_gateway(monkeypatch)
    owner_id = uuid.uuid4()
    caller_id = await _user_id("user@example.com")
    async with AsyncSessionLocal() as session:
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
                external_id="cv-opt-other",
                title="Offre d'un autre",
                url="https://example.com/cv-opt-other",
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

    r = await client.post(_generate_url(match.id), headers=auth_headers)
    assert r.status_code == 404


async def test_get_cv_optimization_404_before_generation(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    match = await _profile_and_match()
    r = await client.get(_generate_url(match.id), headers=auth_headers)
    assert r.status_code == 404


async def test_get_cv_optimization_returns_cached_result(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _mock_gateway(monkeypatch)
    match = await _profile_and_match()
    await client.post(_generate_url(match.id), headers=auth_headers)

    r = await client.get(_generate_url(match.id), headers=auth_headers)

    assert r.status_code == 200
    assert r.json()["headline"] == "Product Owner orienté Data"


async def test_confirm_cv_optimization_requires_auth(client: AsyncClient) -> None:
    r = await client.post(_confirm_url(uuid.uuid4()))
    assert r.status_code == 401


async def test_confirm_cv_optimization_404_before_generation(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    match = await _profile_and_match()
    r = await client.post(_confirm_url(match.id), headers=auth_headers)
    assert r.status_code == 404


async def test_confirm_cv_optimization_sets_confirmed_at(
    client: AsyncClient, auth_headers: dict[str, str], monkeypatch
) -> None:
    _mock_gateway(monkeypatch)
    match = await _profile_and_match()
    await client.post(_generate_url(match.id), headers=auth_headers)

    r = await client.post(_confirm_url(match.id), headers=auth_headers)

    assert r.status_code == 200
    assert r.json()["confirmed_at"] is not None
