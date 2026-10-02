"""Shared date-parsing helpers.

Moved here (2026-10-02) from app/modules/offers/providers/_util.py once a
second module (matching/simplyhired_gateway.py) needed the exact same
ISO-8601 parsing -- a module-internal helper can't be imported across module
boundaries (see tests/test_architecture.py), and this logic has nothing
offers-specific about it anyway.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

_FRENCH_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}
_FRENCH_DATE_RE = re.compile(
    r"(\d{1,2})\s+([a-zéû]+)\s+(\d{4})", re.IGNORECASE
)


def parse_french_date(value: str | None) -> datetime | None:
    """Best-effort parse of a French long-form date such as "7 septembre
    2026" -- the only date format Indeed's review pages expose (no ISO
    attribute anywhere on the page, verified 2026-10-02; see
    indeed_gateway.py). Returns None rather than raising on anything that
    doesn't match, same convention as parse_iso_datetime below."""
    if not value:
        return None
    match = _FRENCH_DATE_RE.search(value.strip().lower())
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _FRENCH_MONTHS.get(month_name)
    if month is None:
        return None
    try:
        return datetime(int(year), month, int(day), tzinfo=UTC)
    except ValueError:
        return None


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Best-effort ISO-8601 parse, shared by every provider's normalizer and
    by simplyhired_gateway.py.

    Returns None rather than raising on a source's occasional malformed or
    missing date -- a bad date shouldn't block ingesting an otherwise-good
    offer (or review).
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
