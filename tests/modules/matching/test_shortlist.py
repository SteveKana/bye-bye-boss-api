from __future__ import annotations

import uuid

from app.modules.cv.models import CandidateProfile
from app.modules.matching.shortlist import shortlist_offers
from app.modules.offers.models import JobOffer


def _profile(**overrides) -> CandidateProfile:
    defaults = {
        "user_id": uuid.uuid4(),
        "embedding": [1.0, 0.0, 0.0],
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _offer(title: str, embedding: list[float] | None, **overrides) -> JobOffer:
    defaults = {
        "source": "test",
        "external_id": str(uuid.uuid4()),
        "title": title,
        "url": "https://example.com",
        "embedding": embedding,
    }
    defaults.update(overrides)
    return JobOffer(**defaults)


def test_shortlist_ranks_by_cosine_similarity() -> None:
    profile = _profile(embedding=[1.0, 0.0, 0.0])
    exact_match = _offer("Product Owner Data", [1.0, 0.0, 0.0])
    somewhat_related = _offer("Chef de projet", [0.7, 0.7, 0.0])
    unrelated = _offer("Boulanger", [0.0, 0.0, 1.0])

    result = shortlist_offers(
        profile, [unrelated, somewhat_related, exact_match], limit=10
    )

    assert result == [exact_match, somewhat_related, unrelated]


def test_shortlist_respects_limit() -> None:
    profile = _profile()
    offers = [_offer(f"Product Owner {i}", [1.0, 0.0, 0.0]) for i in range(5)]

    result = shortlist_offers(profile, offers, limit=2)

    assert len(result) == 2


def test_shortlist_returns_empty_when_profile_has_no_embedding() -> None:
    profile = _profile(embedding=None)
    offer = _offer("Product Owner Data", [1.0, 0.0, 0.0])

    assert shortlist_offers(profile, [offer], limit=10) == []


def test_shortlist_sorts_offers_with_no_embedding_last() -> None:
    """An offer that failed to embed (API error, or ingested before this
    feature shipped and not yet refreshed) is never dropped outright -- the
    LLM scoring step is the real judge of fit, this is only a cheap
    pre-filter -- but it shouldn't outrank an offer with a real signal."""
    profile = _profile(embedding=[1.0, 0.0, 0.0])
    has_embedding = _offer("Chef de projet", [0.1, 0.9, 0.0])
    no_embedding = _offer("Product Owner", None)

    result = shortlist_offers(profile, [no_embedding, has_embedding], limit=10)

    assert result == [has_embedding, no_embedding]
