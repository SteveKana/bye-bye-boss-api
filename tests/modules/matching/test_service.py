from __future__ import annotations

import uuid
from datetime import timedelta

from app.core import embeddings
from app.core.database import AsyncSessionLocal
from app.core.models import utcnow
from app.modules.cv.models import CandidateProfile, ProfileStatus
from app.modules.cv.repository import CandidateProfileRepository
from app.modules.matching.models import CandidateMatch, MatchStatus
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.service import MatchingService
from app.modules.offers.models import JobOffer
from app.modules.offers.repository import JobOfferRepository


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


async def _match_for(profile, offer):
    async with AsyncSessionLocal() as session:
        return await CandidateMatchRepository(session).get_by_profile_and_offer(
            profile.id, offer.id
        )


async def test_first_run_records_visible_placeholders_then_hidden_shortlist() -> None:
    """A brand-new profile's first run: the closest offers are recorded, the
    first MATCHING_MAX_OFFERS_PER_CANDIDATE (5) as visible placeholders (shown
    straight away, no score yet), the rest of the 30-offer pool as hidden
    `shortlisted` rows waiting for the pre-filter. Nothing is scored here."""
    profile = await _make_complete_profile()
    offers = [await _make_offer(title=f"Product Owner Data {i}") for i in range(8)]

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_shortlisted == 8
    statuses = []
    for offer in offers:
        match = await _match_for(profile, offer)
        assert match is not None
        assert (match.career_score, match.ats_score, match.ats_potential) == (0, 0, 0)
        assert match.analysis == {}
        statuses.append(match.status)
    assert statuses.count(MatchStatus.placeholder.value) == 5
    assert statuses.count(MatchStatus.shortlisted.value) == 3


async def test_run_shortlists_at_most_the_prefilter_pool_size(monkeypatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "MATCHING_PREFILTER_POOL_SIZE", 3)
    profile = await _make_complete_profile()
    for i in range(6):
        await _make_offer(title=f"Offre {i}")

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_shortlisted == 3


async def test_run_for_profile_computes_and_caches_profile_embedding_when_missing(
    monkeypatch,
) -> None:
    """A profile with no cached embedding yet -- never matched before, or one
    whose CV was just re-imported -- gets one computed from its CV text and
    persisted before shortlisting even runs."""
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
    assert report.pairs_shortlisted == 1

    async with AsyncSessionLocal() as session:
        refreshed = await CandidateProfileRepository(session).get(profile.id)
    assert refreshed is not None
    assert refreshed.embedding == [1.0, 0.0, 0.0]
    assert await _match_for(profile, offer) is not None


async def test_offers_published_more_than_max_age_days_ago_are_never_shortlisted() -> (
    None
):
    profile = await _make_complete_profile()
    fresh = await _make_offer(
        title="Publiée hier", published_at=utcnow() - timedelta(days=1)
    )
    # Ingested just now, but published three weeks ago (a source re-listing
    # an old ad): must not reach a candidate, first run included.
    stale = await _make_offer(
        title="Publiée il y a 3 semaines", published_at=utcnow() - timedelta(days=21)
    )

    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)

    assert await _match_for(profile, fresh) is not None
    assert await _match_for(profile, stale) is None


async def test_daily_run_only_considers_recent_unseen_offers() -> None:
    profile = await _make_complete_profile()
    first = await _make_offer(title="Offre déjà vue")

    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)
    first_match = await _match_for(profile, first)
    assert first_match is not None

    # Next day: a new offer, plus one ingested long ago that was simply never
    # shortlisted (older than the daily window, so not looked at again).
    new = await _make_offer(title="Offre du jour")
    old = await _make_offer(
        title="Offre ancienne", created_at=utcnow() - timedelta(days=10)
    )

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_shortlisted == 1  # only the new one
    new_match = await _match_for(profile, new)
    assert new_match is not None
    assert new_match.status == MatchStatus.shortlisted.value  # not a first run
    assert await _match_for(profile, old) is None
    # The already-seen pair was left alone, same row.
    again = await _match_for(profile, first)
    assert again is not None and again.id == first_match.id


async def test_cv_change_sends_existing_matches_back_through_the_pipeline() -> None:
    """A re-imported CV clears the cached embedding (see cv/service.py): the
    stored results no longer reflect it, so the closest offers re-enter the
    pipeline -- keeping the same row (and the candidate's application status),
    and staying visible meanwhile instead of vanishing."""
    profile = await _make_complete_profile()
    offer = await _make_offer()
    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)

    async with AsyncSessionLocal() as session:
        repo = CandidateMatchRepository(session)
        match = await repo.get_by_profile_and_offer(profile.id, offer.id)
        await repo.update(
            match,
            {
                "status": MatchStatus.scored.value,
                "career_score": 80,
                "application_status": "applied",
            },
        )
        await session.commit()
        match_id = match.id

    async with AsyncSessionLocal() as session:
        profile = await CandidateProfileRepository(session).update(
            profile, {"embedding": None}
        )

    async def _embed(text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    from unittest import mock

    with mock.patch.object(embeddings, "get_embedding", _embed):
        async with AsyncSessionLocal() as session:
            await MatchingService(session).run_for_profile(profile)

    after = await _match_for(profile, offer)
    assert after is not None
    assert after.id == match_id
    assert after.status == MatchStatus.placeholder.value
    assert after.application_status == "applied"


async def test_run_for_profile_skips_when_no_raw_text() -> None:
    profile = await _make_complete_profile(raw_text=None)
    await _make_offer()

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.profiles_skipped_no_cv_text == 1
    assert report.pairs_shortlisted == 0


async def test_run_for_profile_ignores_saved_mobility_restriction() -> None:
    """Candidates are matched on their CV alone (Steve, 2026-10-05): a
    "Région uniquement" mobility saved in an older profile no longer hides
    offers from other régions -- the candidate filters on the Opportunités
    page instead."""
    profile = await _make_complete_profile(
        mobility="Région uniquement", mobility_region="Bretagne"
    )
    offer = await _make_offer(region="Occitanie")

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.pairs_shortlisted == 1
    assert await _match_for(profile, offer) is not None


_EXPERIENCED = {
    "total_experience": "7 ans",
    "experiences": [{"title": "Product Owner", "company": "Acme"}],
}
_STUDENT = {
    "total_experience": "1 an",
    "experiences": [{"title": "Stagiaire produit", "company": "Acme"}],
}


async def test_experienced_candidate_is_never_shortlisted_stage_or_junior() -> None:
    """Seniority rule (Steve, 2026-10-05): no Stage/Alternance for someone with
    experience, no Junior from 3 years -- decided before any LLM call, the
    pair is recorded as `filtered_out` so it is never evaluated or billed."""
    profile = await _make_complete_profile(**_EXPERIENCED)
    stage = await _make_offer(title="Stage chef de projet", contract_type="Stage")
    alternance = await _make_offer(title="Alternance RH", contract_type="Alternance")
    junior = await _make_offer(title="Product Owner Junior")
    normal = await _make_offer(title="Product Owner confirmé")

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.seniority_excluded == 3
    assert report.pairs_shortlisted == 1
    for offer in (stage, alternance, junior):
        match = await _match_for(profile, offer)
        assert match is not None
        assert match.status == MatchStatus.filtered_out.value
    kept = await _match_for(profile, normal)
    assert kept is not None
    assert kept.status != MatchStatus.filtered_out.value


async def test_student_candidate_still_receives_stage_and_junior_offers() -> None:
    profile = await _make_complete_profile(**_STUDENT)
    stage = await _make_offer(title="Stage produit", contract_type="Stage")
    junior = await _make_offer(title="Product Owner Junior")

    async with AsyncSessionLocal() as session:
        report = await MatchingService(session).run_for_profile(profile)

    assert report.seniority_excluded == 0
    assert report.pairs_shortlisted == 2
    for offer in (stage, junior):
        match = await _match_for(profile, offer)
        assert match is not None
        assert match.status != MatchStatus.filtered_out.value


async def test_existing_stage_match_is_hidden_unless_already_applied() -> None:
    profile = await _make_complete_profile(**_EXPERIENCED)
    open_stage = await _make_offer(title="Stage A", contract_type="Stage")
    applied_stage = await _make_offer(title="Stage B", contract_type="Stage")
    async with AsyncSessionLocal() as session:
        repo = CandidateMatchRepository(session)
        for offer, application in (
            (open_stage, "not_applied"),
            (applied_stage, "applied"),
        ):
            await repo.create(
                CandidateMatch(
                    candidate_profile_id=profile.id,
                    job_offer_id=offer.id,
                    career_score=70,
                    ats_score=70,
                    ats_potential=80,
                    computed_at=utcnow(),
                    status=MatchStatus.scored.value,
                    application_status=application,
                )
            )
        await session.commit()

    async with AsyncSessionLocal() as session:
        await MatchingService(session).run_for_profile(profile)

    hidden = await _match_for(profile, open_stage)
    kept = await _match_for(profile, applied_stage)
    assert hidden is not None and kept is not None
    assert hidden.status == MatchStatus.filtered_out.value
    assert kept.status == MatchStatus.scored.value
