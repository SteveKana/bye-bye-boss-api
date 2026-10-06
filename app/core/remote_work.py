"""Best-effort detection of "full remote" postings from free text.

Neither France Travail nor Adzuna exposes a structured remote-work field
(see offers/models.py's `remote_policy` docstring) -- so without this, the
"Full remote" filter on the Opportunités page would miss genuinely
fully-remote offers.

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


# --- Hybrid ("N jours de télétravail / sur site / en présentiel") -----------
#
# An offer that spells out a split of days between home and the office, or
# says "hybride" / "télétravail partiel". Same caveat as above: a keyword
# heuristic that fails both ways -- an offer silent about remote work is never
# flagged hybrid. A fully-remote offer is never hybrid (see `looks_hybrid`).

_DAYS_NUMBER = r"(?:\d+|un|une|deux|trois|quatre|cinq)"
_DAYS = (
    rf"(?:\b{_DAYS_NUMBER}\s*(?:(?:à|a|-|/|ou)\s*{_DAYS_NUMBER}\s*)?"
    r"(?:jours?|j)\b)"
)
_WORK_PLACE = (
    r"(?:t[ée]l[ée]travail|t[ée]l[ée]-travail|remote|distanciel|à\s+distance|"
    r"sur\s+site|sur\s+place|en\s+pr[ée]sentiel|pr[ée]sentiel|au\s+bureau|"
    r"chez\s+le\s+client|dans\s+nos\s+locaux|en\s+agence)"
)

_HYBRID_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # "3 jours sur site", "2 jours de télétravail par semaine", "1 jour en
        # présentiel": days first, place within a few words after.
        rf"{_DAYS}[^.\n;]{{0,30}}?{_WORK_PLACE}",
        # "télétravail : 2 jours par semaine", "présentiel 3j": place first.
        rf"{_WORK_PLACE}[^.\n;]{{0,30}}?{_DAYS}",
        r"\bhybrid(?:e|es)?\b",
        r"t[ée]l[ée]travail\s+(?:partiel|hybride|[àa]\s+temps\s+partiel)",
        r"mode\s+(?:de\s+travail\s+)?mixte",
    )
]


def looks_hybrid(*texts: str | None) -> bool:
    """True if any of the given texts (typically an offer's title and
    description) describes a hybrid arrangement -- a split of days between
    remote work and the office. A posting that already reads as fully remote
    is never hybrid."""
    combined = " ".join(text for text in texts if text)
    if not combined or looks_full_remote(combined):
        return False
    return any(pattern.search(combined) for pattern in _HYBRID_PATTERNS)
