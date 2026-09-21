"""Small text-normalization helpers shared across modules.

Kept here (not in a specific module) because both `offers` (deriving a
région from a source's raw location string) and `matching` (comparing a
candidate's free-text `location`/`mobility_region` against an offer's) need
the same accent/case-insensitive comparison, and neither module depends on
the other -- see app/modules/matching/geo_filter.py's docstring for why
this couldn't just live in one of them.
"""

from __future__ import annotations

import unicodedata


def fold(text: str | None) -> str:
    """Lowercase and strip accents/diacritics, so "Île-de-France",
    "ile de france" and "ÎLE-DE-FRANCE" all compare equal. Punctuation and
    whitespace are left as-is (only collapsed/stripped at the edges) since
    callers compare whole strings or use substring containment, not a token
    match."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    without_accents = "".join(c for c in normalized if not unicodedata.combining(c))
    return without_accents.lower().strip()
