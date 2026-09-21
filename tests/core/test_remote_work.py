from __future__ import annotations

from app.core.remote_work import looks_full_remote


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
