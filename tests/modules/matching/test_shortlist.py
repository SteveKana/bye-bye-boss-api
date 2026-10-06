from __future__ import annotations

import math
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


def test_shortlist_normalizes_scores_per_source() -> None:
    """A standout offer from a source whose postings are systematically
    thinner in content (e.g. Adzuna's free-tier API truncates descriptions
    to ~500 characters vs France Travail's ~2500 -- see this module's
    docstring) shouldn't be buried under every offer from a richer source
    just because its raw similarity trails them all. Comparing each offer
    to its own source's mean/stdev lets a genuine standout compete even
    when its raw score sits below a whole cluster from another source."""
    profile = _profile(embedding=[1.0, 0.0, 0.0])

    def _at(cosine: float) -> list[float]:
        return [cosine, math.sqrt(1 - cosine * cosine), 0.0]

    # france_travail clusters at raw ~0.7-0.9; adzuna clusters much lower
    # (~0.1-0.5), but 0.5 is a clear outlier *above its own peers* --
    # despite trailing every single france_travail offer in raw terms.
    ft_high = _offer("FT haut", _at(0.9), source="france_travail")
    ft_mid = _offer("FT moyen", _at(0.8), source="france_travail")
    ft_low = _offer("FT bas", _at(0.7), source="france_travail")
    adzuna_standout = _offer("Adzuna standout", _at(0.5), source="adzuna")
    adzuna_mid = _offer("Adzuna moyen", _at(0.2), source="adzuna")
    adzuna_low = _offer("Adzuna bas", _at(0.1), source="adzuna")

    result = shortlist_offers(
        profile,
        [ft_high, ft_mid, ft_low, adzuna_standout, adzuna_mid, adzuna_low],
        limit=10,
    )

    # Raw similarity alone would rank every france_travail offer ahead of
    # every adzuna one. Normalized per source, adzuna's outlier now beats
    # even france_travail's best.
    assert result[0] == adzuna_standout
    assert result.index(adzuna_standout) < result.index(ft_high)


def test_shortlist_handles_single_offer_source_without_crashing() -> None:
    """A source with only one offer (or every offer tied) has zero
    variance -- normalizing against it must not divide by zero."""
    profile = _profile(embedding=[1.0, 0.0, 0.0])
    only_offer = _offer("Unique", [1.0, 0.0, 0.0], source="solo")

    result = shortlist_offers(profile, [only_offer], limit=10)

    assert result == [only_offer]
