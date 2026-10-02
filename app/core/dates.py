"""Shared date-parsing helpers.

Moved here (2026-10-02) from app/modules/offers/providers/_util.py once a
second module (matching/simplyhired_gateway.py) needed the exact same
ISO-8601 parsing -- a module-internal helper can't be imported across module
boundaries (see tests/test_architecture.py), and this logic has nothing
offers-specific about it anyway.
"""

from __future__ import annotations

from datetime import datetime


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
