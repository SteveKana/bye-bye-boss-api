from __future__ import annotations

from datetime import datetime


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Best-effort ISO-8601 parse, shared by every provider's normalizer.

    Returns None rather than raising on a source's occasional malformed or
    missing date -- a bad date shouldn't block ingesting an otherwise-good
    offer.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
