from __future__ import annotations

import uuid

from app.modules.cv.models import CandidateProfile
from app.modules.matching.shortlist import shortlist_offers
from app.modules.offers.models import JobOffer


def _profile(**overrides) -> CandidateProfile:
    defaults = {
        "user_id": uuid.uuid4(),
        "headline": "Product Owner",
        "identified_roles": ["Product Owner", "Product Manager"],
        "domains": ["Data", "Retail"],
        "skills": ["SQL", "Agile", "Backlog Management"],
    }
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _offer(title: str, description: str = "", **overrides) -> JobOffer:
    defaults = {
        "source": "test",
        "external_id": str(uuid.uuid4()),
        "title": title,
        "description": description,
        "url": "https://example.com",
    }
    defaults.update(overrides)
    return JobOffer(**defaults)


def test_shortlist_excludes_offers_with_no_keyword_overlap() -> None:
    profile = _profile()
    relevant = _offer("Product Owner Data H/F", "Gestion du backlog, SQL, Agile.")
    irrelevant = _offer("Boulanger H/F", "Pétrissage, cuisson, vente en boutique.")

    result = shortlist_offers(profile, [relevant, irrelevant], limit=10)

    assert relevant in result
    assert irrelevant not in result


def test_shortlist_ranks_by_keyword_overlap() -> None:
    profile = _profile()
    strong_match = _offer(
        "Product Owner Data",
        "Product Owner en charge du backlog, SQL et méthodologie Agile.",
    )
    weak_match = _offer("Chef de projet SI", "Pilotage de projets divers.")
    # weak_match only overlaps on nothing here -- give it exactly one hit.
    weak_match.description = "Quelques notions de SQL sont un plus."

    result = shortlist_offers(profile, [weak_match, strong_match], limit=10)

    assert result[0] is strong_match


def test_shortlist_respects_limit() -> None:
    profile = _profile()
    offers = [_offer(f"Product Owner {i}", "Backlog, Agile, SQL") for i in range(5)]

    result = shortlist_offers(profile, offers, limit=2)

    assert len(result) == 2


def test_shortlist_returns_empty_when_profile_has_no_keywords() -> None:
    profile = _profile(headline=None, identified_roles=[], domains=[], skills=[])
    offer = _offer("Product Owner Data", "Backlog, Agile, SQL")

    assert shortlist_offers(profile, [offer], limit=10) == []
