"""Best-effort detection of "full remote" postings from free text.

Neither France Travail nor Adzuna exposes a structured remote-work field
(see offers/models.py's `remote_policy` docstring) -- so without this, a
candidate who restricted their search to a région or a city (see
matching/geo_filter.py) would never see a genuinely fully-remote offer just
because it happens to be posted from a distant office.

This is a keyword heuristic, not a guarantee, and it can fail both ways:
an offer that describes full-remote work in wording not covered here is a
false negative (silently geo-filtered out like any other out-of-zone offer
-- no worse than before this feature existed, but no better either); one
that mentions remote work only loosely could in principle be a false
positive, though the patterns below require an explicit "full/100%/total"
qualifier specifically to avoid matching the far more common "1-2 jours de
télétravail par semaine" (hybrid) style listing on the bare word
"télétravail" alone.
"""

from __future__ import annotations

import re

_FULL_REMOTE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"full[\s-]?remote",
        r"remote[\s-]?first",
        r"remote[\s-]?only",
        r"100\s?%\s*(du\s+temps\s+)?(en\s+)?(t[ée]l[ée]travail|remote|distanciel)",
        r"(t[ée]l[ée]travail|remote|distanciel)\s*(à\s*)?100\s?%",
        r"t[ée]l[ée]travail\s+(total|complet|int[ée]gral)",
        r"full[\s-]?t[ée]l[ée]travail",
        r"enti[èe]rement\s+(en\s+)?(t[ée]l[ée]travail|remote|à\s+distance)",
        r"travail\s+100\s?%\s+à\s+distance",
    )
]


def looks_full_remote(*texts: str | None) -> bool:
    """True if any of the given texts (typically an offer's title and
    description) reads as a fully-remote posting."""
    combined = " ".join(text for text in texts if text)
    return any(pattern.search(combined) for pattern in _FULL_REMOTE_PATTERNS)
