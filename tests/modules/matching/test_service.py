from __future__ import annotations

import uuid

from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching import gateway
from app.modules.matching.llm_schema import LLMAnalysis
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.service import MatchingService
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository

_FAKE_ANALYSIS = LLMAnalysis(
    company_name="Astek",
    career_score=80,
    ats_score=60,
    ats_potential=75,
)


async def _make_complete_profile(**overrides) -> CandidateProfile:
    defaults = {
        "user_id": uuid.uuid4(),
        "status": ProfileStatus.complete.value,
        "raw_text": "Steve Kana, Product Owner, SQL, Agile, backlog management.",
        "headline": "Product Owner",
        "identified_roles": ["Product Owner"],
        "domains": ["Data"],
        "skills": ["SQL", "Agile", "Backlog"],
    }
    defaults.update(overrides)
    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).create(
            CandidateProfile(**defaults)
        )
        await session.commit()
        return profile


async def _make_offer(**overrides) -> JobOffer:
    defaults = {
        "source": "test",
        "external_id": str(uuid.uuid4()),
        "title": "Product Owner Data",
        "description": "Backlog, SQL, Agile, méthodologie SAFe.",
        "url": "https://example.com/offre",
    }
    defaults.update(overrides)
    async with AsyncSessionLocal() as session:
        offer = await JobOfferRepository(session).create(JobOffer(**defaults))
        await session.commit()
        return offer


async def test_run_for_profile_creates_a_match(monkeypatch) -> None:
    async def _fake(cv, offer):
        return _FAKE_ANALYSIS

    monkeypatch.setattr(gateway, "analyse_match", _fake)

    profile = await _make_complete_profile()
    offer = await _make_offer()

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_scored == 1
    assert report.pairs_failed == 0

    async with AsyncSessionLocal() as session:
        match = await CandidateMatchRepository(session).get_by_profile_and_offer(
            profile.id, offer.id
        )
    assert match is not None
    assert match.career_score == 80
    assert match.ats_score == 60
    assert match.company_name == "Astek"


async def test_run_for_profile_skips_irrelevant_offers(monkeypatch) -> None:
    calls = []

    async def _fake(cv, offer):
        calls.append(offer)
        return _FAKE_ANALYSIS

    monkeypatch.setattr(gateway, "analyse_match", _fake)

    profile = await _make_complete_profile()
    await _make_offer(
        title="Boulanger H/F", description="Pétrissage et cuisson artisanale."
    )

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_scored == 0
    assert calls == []


async def test_run_for_profile_skips_already_fresh_matches(monkeypatch) -> None:
    calls = 0

    async def _fake(cv, offer):
        nonlocal calls
        calls += 1
        return _FAKE_ANALYSIS

    monkeypatch.setattr(gateway, "analyse_match", _fake)

    profile = await _make_complete_profile()
    await _make_offer()

    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)
    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert calls == 1  # only the first run actually called the LLM
    assert report.pairs_skipped_fresh == 1
    assert report.pairs_scored == 0


async def test_run_for_profile_recomputes_when_offer_changes(monkeypatch) -> None:
    calls = 0

    async def _fake(cv, offer):
        nonlocal calls
        calls += 1
        return _FAKE_ANALYSIS

    monkeypatch.setattr(gateway, "analyse_match", _fake)

    profile = await _make_complete_profile()
    offer = await _make_offer()

    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)

    # Simulate the offer being refreshed by ingestion after the first match.
    async with AsyncSessionLocal() as session:
        repo = JobOfferRepository(session)
        stale = await repo.get(offer.id)
        await repo.update(stale, {"updated_at": utcnow()})
        await session.commit()

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert calls == 2
    assert report.pairs_scored == 1


async def test_run_for_profile_skips_when_no_raw_text(monkeypatch) -> None:
    async def _fake(cv, offer):
        raise AssertionError("should never be called")

    monkeypatch.setattr(gateway, "analyse_match", _fake)

    profile = await _make_complete_profile(raw_text=None)
    await _make_offer()

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.profiles_skipped_no_cv_text == 1
    assert report.pairs_scored == 0
