"""Small text-normalization helpers shared across modules.

Kept here (not in a specific module) because several modules (`offers`
deriving a région from a source's raw location string, `matching`
comparing company names) need the same accent/case-insensitive comparison.
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


def strip_nul(value):
    """Remove NUL (\\x00) characters from a string, recursively through the
    lists and dicts of a parsed document. PostgreSQL refuses them in text
    and JSON columns, and some PDF exports leave them in the extracted text
    (a CV with "2024" wrapped in NULs made every upload fail with a 500)."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [strip_nul(item) for item in value]
    if isinstance(value, dict):
        return {key: strip_nul(item) for key, item in value.items()}
    return value
