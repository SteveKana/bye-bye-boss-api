from __future__ import annotations

from app.core.regions import (
    FRENCH_REGIONS,
    normalize_region_name,
    region_from_insee_code,
    region_from_postal_code,
)


def test_region_from_postal_code_metropolitan() -> None:
    assert region_from_postal_code("75001") == "Île-de-France"
    assert region_from_postal_code("69003") == "Auvergne-Rhône-Alpes"


def test_region_from_postal_code_corse_shares_postal_prefix() -> None:
    # Both Corse départements (2A, 2B) use the "20" postal prefix.
    assert region_from_postal_code("20000") == "Corse"  # Ajaccio, 2A
    assert region_from_postal_code("20200") == "Corse"  # Bastia, 2B


def test_region_from_postal_code_overseas() -> None:
    assert region_from_postal_code("97400") == "La Réunion"
    assert region_from_postal_code("97200") == "Martinique"


def test_region_from_postal_code_handles_missing_or_junk() -> None:
    assert region_from_postal_code(None) is None
    assert region_from_postal_code("") is None
    assert region_from_postal_code("abc") is None
    # An unrecognized/nonexistent département code.
    assert region_from_postal_code("99999") is None


def test_region_from_insee_code_corse_uses_letter_suffix() -> None:
    # Unlike postal codes, INSEE codes spell Corse's départements as 2A/2B.
    assert region_from_insee_code("2A004") == "Corse"
    assert region_from_insee_code("2b033") == "Corse"  # lowercase input


def test_region_from_insee_code_overseas_and_metropolitan() -> None:
    assert region_from_insee_code("97302") == "Guyane"
    assert region_from_insee_code("75056") == "Île-de-France"


def test_normalize_region_name_handles_accents_and_case() -> None:
    assert normalize_region_name("ile de france") == "Île-de-France"
    assert normalize_region_name("ÎLE-DE-FRANCE") == "Île-de-France"
    assert normalize_region_name("bretagne") == "Bretagne"


def test_normalize_region_name_rejects_non_region_strings() -> None:
    # Adzuna's breadcrumb includes the country and city too -- neither
    # should ever resolve to a région.
    assert normalize_region_name("France") is None
    assert normalize_region_name("Paris") is None
    assert normalize_region_name(None) is None


def test_normalize_region_name_covers_every_canonical_region() -> None:
    for region in FRENCH_REGIONS:
        assert normalize_region_name(region) == region
