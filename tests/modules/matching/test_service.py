from __future__ import annotations

import asyncio
import uuid

from app.core import embeddings
from app.core.config import get_settings
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
        # A pre-set embedding bypasses the lazy-compute-via-API path (see
        # MatchingService._run_for_profile) so most tests here exercise the
        # shortlisting/scoring logic without also depending on the
        # embeddings gateway -- that path has its own dedicated test below
        # (test_run_for_profile_computes_and_caches_profile_embedding_when_missing).
        "embedding": [1.0, 0.0, 0.0],
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
        # Same vector as _make_complete_profile's default -- a strong cosine
        # match, so the default offer is always shortlisted.
        "embedding": [1.0, 0.0, 0.0],
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


async def test_run_for_profile_computes_and_caches_profile_embedding_when_missing(
    monkeypatch,
) -> None:
    """A profile with no cached embedding yet -- never matched before, or one
    that already existed before this feature shipped -- gets one computed
    from its CV text and persisted before shortlisting even runs (see
    MatchingService._run_for_profile).

    This replaces the old v1-heuristic test that used to assert a plainly
    irrelevant offer never got scored at all. Under the embeddings design
    there's no such hard cutoff (see shortlist.py's docstring: the LLM step
    is the real judge of fit, this is only a cheap pre-filter) -- shortlist's
    own ranking/ordering behaviour is covered by test_shortlist.py instead.
    """

    async def _fake_analyse(cv, offer):
        return _FAKE_ANALYSIS

    monkeypatch.setattr(gateway, "analyse_match", _fake_analyse)

    embed_calls: list[str] = []

    async def _fake_embed(text: str) -> list[float]:
        embed_calls.append(text)
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(embeddings, "get_embedding", _fake_embed)

    profile = await _make_complete_profile(embedding=None)
    offer = await _make_offer()

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert embed_calls == [profile.raw_text]
    assert report.pairs_scored == 1

    async with AsyncSessionLocal() as session:
        refreshed = await CandidateProfileRepository(session).get(profile.id)
    assert refreshed is not None
    assert refreshed.embedding == [1.0, 0.0, 0.0]

    async with AsyncSessionLocal() as session:
        match = await CandidateMatchRepository(session).get_by_profile_and_offer(
            profile.id, offer.id
        )
    assert match is not None


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


async def test_run_for_profile_scores_offers_concurrently_within_limit(
    monkeypatch,
) -> None:
    """Offers are scored several at a time (bounded by MATCHING_CONCURRENCY)
    instead of one at a time -- this is what cuts a run's wall-clock time;
    see service.py's docstring on _run_for_profile. Verifies the concurrency
    cap is actually respected and that every pair still gets a correct,
    isolated write despite running concurrently."""
    monkeypatch.setattr(get_settings(), "MATCHING_CONCURRENCY", 2)

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def _fake(cv, offer):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return _FAKE_ANALYSIS

    monkeypatch.setattr(gateway, "analyse_match", _fake)

    profile = await _make_complete_profile()
    offers = [await _make_offer(title=f"Product Owner Data {i}") for i in range(5)]

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_scored == 5
    assert report.pairs_failed == 0
    assert max_in_flight == 2  # never exceeded the configured cap

    async with AsyncSessionLocal() as session:
        repo = CandidateMatchRepository(session)
        for offer in offers:
            match = await repo.get_by_profile_and_offer(profile.id, offer.id)
            assert match is not None
            assert match.career_score == 80
