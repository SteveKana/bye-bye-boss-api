from __future__ import annotations

from app.core.daily_rate import extract_daily_rate


def test_detects_explicit_tjm_single_value() -> None:
    assert extract_daily_rate("Poste freelance. TJM : 550€.") == (550, 550)
    assert extract_daily_rate("TJM 500 euros, à négocier.") == (500, 500)


def test_detects_explicit_tjm_range() -> None:
    assert extract_daily_rate("Mission freelance. TJM : 450-550€.") == (450, 550)
    assert extract_daily_rate("TJM entre 400€ et 500€.") == (400, 500)


def test_detects_taux_journalier_moyen_wording() -> None:
    assert extract_daily_rate("Taux journalier moyen : 600€.") == (600, 600)
    assert extract_daily_rate("Taux journalier : 400-500€ selon profil.") == (400, 500)


def test_detects_bare_per_day_pattern() -> None:
    assert extract_daily_rate("Rémunération : 550€/jour.") == (550, 550)
    assert extract_daily_rate("450€ à 550€/jour selon expérience.") == (450, 550)
    assert extract_daily_rate("500 € / jour") == (500, 500)


def test_ignores_amounts_outside_plausible_tjm_range() -> None:
    # A per diem or an unrelated figure stated the same way ("50€/jour")
    # should not be mistaken for a TJM -- see module docstring.
    assert extract_daily_rate("Panier repas : 50€/jour.") == (None, None)
    assert extract_daily_rate("TJM : 50€.") == (None, None)
    assert extract_daily_rate("TJM : 5000€.") == (None, None)


def test_combines_multiple_texts() -> None:
    assert extract_daily_rate(None, "Mission freelance.", "TJM : 500€") == (500, 500)


def test_handles_missing_text() -> None:
    assert extract_daily_rate(None) == (None, None)
    assert extract_daily_rate(None, None) == (None, None)
    assert extract_daily_rate("") == (None, None)


def test_no_match_returns_none() -> None:
    assert extract_daily_rate("Poste en CDI, salaire selon profil.") == (None, None)


def test_prefers_tjm_label_over_generic_per_day_pattern() -> None:
    # Both patterns could match here; the explicit TJM figure must win, not
    # some other "/jour" figure appearing elsewhere in the text.
    text = "Frais de déplacement : 40€/jour. TJM : 600€."
    assert extract_daily_rate(text) == (600, 600)
