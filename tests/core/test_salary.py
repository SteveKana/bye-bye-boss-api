from __future__ import annotations

from app.core.salary import extract_annual_salary


def test_detects_annuel_range() -> None:
    assert extract_annual_salary("Annuel de 40000.0 à 48000.0 Euros") == (40000, 48000)
    label = "Annuel de 45000.0 Euros à 55000.0 Euros - TR, CSE"
    assert extract_annual_salary(label) == (45000, 55000)


def test_detects_annuel_single_value() -> None:
    assert extract_annual_salary("Annuel de 42000.0 Euros") == (42000, 42000)


def test_annualizes_mensuel() -> None:
    label = "Mensuel de 2500.0 Euros à 3000.0 Euros"
    assert extract_annual_salary(label) == (30000, 36000)
    assert extract_annual_salary("Mensuel de 2500.0 Euros") == (30000, 30000)


def test_annualizes_horaire() -> None:
    # 15€/h * 151.67 h/month * 12 months ≈ 27300; 18€/h ≈ 32761.
    low, high = extract_annual_salary("Horaire de 15.0 Euros à 18.0 Euros")
    assert low == 27301
    assert high == 32761


def test_ignores_amounts_outside_plausible_annual_range() -> None:
    assert extract_annual_salary("Annuel de 500.0 Euros") == (None, None)
    assert extract_annual_salary("Annuel de 900000.0 Euros") == (None, None)


def test_handles_missing_or_unrecognized_label() -> None:
    assert extract_annual_salary(None) == (None, None)
    assert extract_annual_salary("") == (None, None)
    assert extract_annual_salary("Selon profil") == (None, None)
