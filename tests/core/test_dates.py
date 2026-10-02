from __future__ import annotations

from app.core.dates import parse_french_date, parse_iso_datetime


def test_parses_zulu_suffixed_timestamp() -> None:
    dt = parse_iso_datetime("2018-12-10T19:01:32.416Z")
    assert dt is not None
    assert dt.year == 2018
    assert dt.month == 12
    assert dt.day == 10


def test_returns_none_for_missing_value() -> None:
    assert parse_iso_datetime(None) is None
    assert parse_iso_datetime("") is None


def test_returns_none_for_malformed_value() -> None:
    assert parse_iso_datetime("not a date") is None


def test_parses_french_long_form_date() -> None:
    dt = parse_french_date("7 septembre 2026")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 9, 7)


def test_parses_french_date_with_accented_month() -> None:
    dt = parse_french_date("3 août 2024")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2024, 8, 3)


def test_french_date_returns_none_for_unknown_month() -> None:
    assert parse_french_date("7 frimaire 2026") is None


def test_french_date_returns_none_for_missing_or_malformed_value() -> None:
    assert parse_french_date(None) is None
    assert parse_french_date("") is None
    assert parse_french_date("pas une date") is None
