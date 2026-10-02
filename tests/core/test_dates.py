from __future__ import annotations

from app.core.dates import parse_iso_datetime


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
