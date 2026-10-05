from __future__ import annotations

import pytest

from app.core.remote_work import looks_full_remote, looks_hybrid


def test_detects_common_full_remote_phrasings() -> None:
    assert looks_full_remote("Développeur Full Remote") is True
    assert looks_full_remote("Poste en télétravail 100%") is True
    assert looks_full_remote("100% télétravail possible") is True
    assert looks_full_remote("Télétravail total, aucune présence requise") is True
    assert looks_full_remote("Remote-first company") is True
    assert looks_full_remote(None, "Travail 100% à distance") is True


def test_does_not_flag_hybrid_or_onsite_offers() -> None:
    hybrid = "Poste hybride, 2 jours de télétravail par semaine"
    assert looks_full_remote(hybrid) is False
    assert looks_full_remote("Travail sur site, présence quotidienne requise") is False
    assert looks_full_remote("Télétravail ponctuel selon accord manager") is False


def test_handles_missing_text() -> None:
    assert looks_full_remote(None) is False
    assert looks_full_remote(None, None) is False
    assert looks_full_remote("") is False


def test_combines_title_and_description() -> None:
    assert looks_full_remote("Chef de projet", "Ce poste est en full remote.") is True


# --- looks_hybrid -----------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "3 jours sur site et 2 jours de télétravail par semaine",
        "Télétravail : 2 jours par semaine",
        "1 jour en présentiel par semaine",
        "Présentiel 3j / semaine",
        "2 à 3 jours de télétravail",
        "deux jours de télétravail possible",
        "Poste hybride, basé à Lyon",
        "Télétravail partiel possible",
        "Mode de travail mixte",
        "4 jours au bureau, 1 jour remote",
        "Hybrid working: 3 days office",
    ],
)
def test_looks_hybrid_detects_day_splits(text: str) -> None:
    assert looks_hybrid(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Poste 100% télétravail",
        "Full remote, partout en France",
        "Travail sur site, présence quotidienne requise",
        "Mission de 6 mois chez le client",
        "Télétravail ponctuel selon accord manager",
        "Expérience de 3 ans minimum",
        "",
        None,
    ],
)
def test_looks_hybrid_ignores_other_postings(text: str | None) -> None:
    assert looks_hybrid(text) is False


def test_a_full_remote_offer_is_never_hybrid() -> None:
    assert looks_hybrid("Full remote, avec 2 jours de télétravail à la carte") is False
