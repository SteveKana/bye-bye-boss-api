from __future__ import annotations

import uuid

from app.modules.cv.models import CandidateProfile
from app.modules.matching.geo_filter import filter_by_geography
from app.modules.offers.models import JobOffer


def _profile(**overrides) -> CandidateProfile:
    defaults = {"user_id": uuid.uuid4()}
    defaults.update(overrides)
    return CandidateProfile(**defaults)


def _offer(**overrides) -> JobOffer:
    defaults = {
        "source": "test",
        "external_id": str(uuid.uuid4()),
        "title": "Offre",
        "url": "https://example.com",
    }
    defaults.update(overrides)
    return JobOffer(**defaults)


def test_no_filtering_when_mobility_is_france_entiere() -> None:
    profile = _profile(mobility="France entière")
    offers = [
        _offer(region="Bretagne"),
        _offer(region=None),
        _offer(region="Corse"),
    ]

    assert filter_by_geography(profile, offers) == offers


def test_no_filtering_when_mobility_is_unset() -> None:
    profile = _profile(mobility=None)
    offers = [_offer(region=None), _offer(region="Corse")]

    assert filter_by_geography(profile, offers) == offers


def test_region_mobility_keeps_only_matching_region() -> None:
    profile = _profile(mobility="Région uniquement", mobility_region="Bretagne")
    in_region = _offer(region="Bretagne")
    other_region = _offer(region="Occitanie")

    result = filter_by_geography(profile, [in_region, other_region])

    assert result == [in_region]


def test_region_mobility_excludes_offer_with_undetermined_region() -> None:
    """An offer whose zone couldn't be derived at ingestion is excluded
    outright, never shown -- explicit product decision (2026-09-21), not a
    permissive fallback."""
    profile = _profile(mobility="Région uniquement", mobility_region="Bretagne")
    undetermined = _offer(region=None)

    assert filter_by_geography(profile, [undetermined]) == []


def test_region_mobility_always_keeps_full_remote_offers() -> None:
    profile = _profile(mobility="Région uniquement", mobility_region="Bretagne")
    remote_elsewhere = _offer(region="Occitanie", is_full_remote=True)
    remote_no_region = _offer(region=None, is_full_remote=True)

    result = filter_by_geography(profile, [remote_elsewhere, remote_no_region])

    assert result == [remote_elsewhere, remote_no_region]


def test_city_mobility_matches_on_shared_place_name() -> None:
    profile = _profile(mobility="Ville uniquement", location="Paris")
    matching = _offer(location="75 - Paris")
    elsewhere = _offer(location="Lyon, Rhône")

    result = filter_by_geography(profile, [matching, elsewhere])

    assert result == [matching]


def test_city_mobility_ignores_generic_country_token() -> None:
    profile = _profile(mobility="Ville uniquement", location="Paris, France")
    matching = _offer(location="Paris")

    assert filter_by_geography(profile, [matching]) == [matching]


def test_city_mobility_excludes_offer_with_no_location() -> None:
    profile = _profile(mobility="Ville uniquement", location="Paris")
    undetermined = _offer(location=None)

    assert filter_by_geography(profile, [undetermined]) == []


def test_city_mobility_excludes_when_profile_has_no_location() -> None:
    profile = _profile(mobility="Ville uniquement", location=None)
    offer = _offer(location="Paris")

    assert filter_by_geography(profile, [offer]) == []


def test_city_mobility_always_keeps_full_remote_offers() -> None:
    profile = _profile(mobility="Ville uniquement", location="Paris")
    remote = _offer(location="Lyon", is_full_remote=True)

    assert filter_by_geography(profile, [remote]) == [remote]


def test_unrecognized_mobility_value_fails_open() -> None:
    """A stored value that predates a future change to the Mobility Literal
    shouldn't silently hide every offer from that candidate."""
    profile = _profile(mobility="Some future value")
    offers = [_offer(region=None), _offer(region="Bretagne")]

    assert filter_by_geography(profile, offers) == offers
