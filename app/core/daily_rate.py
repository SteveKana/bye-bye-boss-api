"""Best-effort extraction of a freelance daily rate (TJM) from free text.

Neither France Travail nor Adzuna exposes a structured daily-rate field --
a freelance/portage mission on these sources states its TJM, when it states
one at all, as plain text within the offer's description or salary label
(e.g. "TJM : 500€", "Taux journalier moyen : 450-550€", "500€/jour"). This
is a keyword+pattern heuristic, not a guarantee, same spirit and same
caveats as core/remote_work.py's looks_full_remote:

  - A TJM stated in wording not covered here is a false negative -- the
    offer simply has no daily_rate_min/max, same as one that never stated
    a figure at all. It stays excluded from anything filtered/sorted by
    daily rate, never guessed.
  - A bare "XXX€/jour" pattern with no "TJM"/"taux journalier" keyword
    nearby could in principle match something stated the same way for a
    different reason (a meal or travel per diem, for instance). The
    plausibility bound below (_MIN_PLAUSIBLE_TJM) mitigates the most common
    case -- a per diem in France is typically well under 150€/day, a real
    freelance TJM essentially never is -- but this is a heuristic, not a
    certainty, and an occasional false positive is possible.

An explicit "TJM"/"taux journalier" label is tried first specifically
because it's the more reliable signal; the bare ".../jour" patterns are the
fallback for a mission that states its rate without using either phrase.
"""

from __future__ import annotations

import re

# A real French freelance TJM is virtually always within this range; a
# number outside it is more likely a mis-match (a per diem, a phone number
# fragment, an unrelated figure) than a genuine daily rate.
_MIN_PLAUSIBLE_TJM = 150
_MAX_PLAUSIBLE_TJM = 2000

_NUM = r"(\d{2,4})"
_EUR = r"(?:€|eur|euros?)"
# "et" alongside the usual à/-/– so "TJM entre 400€ et 500€" is recognized,
# not just the "TJM : 400-500€" hyphen style.
_RANGE_SEP = r"(?:à|-|–|et)"
_RANGE_PREFIX = r"(?:de\s*|entre\s*)?"

_TJM_RANGE = re.compile(
    rf"TJM\s*:?\s*{_RANGE_PREFIX}{_NUM}\s*{_EUR}?\s*{_RANGE_SEP}\s*{_NUM}\s*{_EUR}?",
    re.IGNORECASE,
)
_TJM_SINGLE = re.compile(
    rf"TJM\s*:?\s*(?:de\s*)?{_NUM}\s*{_EUR}?",
    re.IGNORECASE,
)
_TAUX_JOURNALIER_RANGE = re.compile(
    rf"taux\s+journalier(?:\s+moyen)?\s*:?\s*{_RANGE_PREFIX}"
    rf"{_NUM}\s*{_EUR}?\s*{_RANGE_SEP}\s*{_NUM}\s*{_EUR}?",
    re.IGNORECASE,
)
_TAUX_JOURNALIER_SINGLE = re.compile(
    rf"taux\s+journalier(?:\s+moyen)?\s*:?\s*(?:de\s*)?{_NUM}\s*{_EUR}?",
    re.IGNORECASE,
)
_PER_DAY_RANGE = re.compile(
    rf"{_NUM}\s*(?:{_EUR}\s*)?{_RANGE_SEP}\s*{_NUM}\s*{_EUR}\s*/\s*j(?:our)?\b",
    re.IGNORECASE,
)
_PER_DAY_SINGLE = re.compile(
    rf"{_NUM}\s*{_EUR}\s*/\s*j(?:our)?\b",
    re.IGNORECASE,
)

# Tried in this order: an explicit "TJM"/"taux journalier" label wins over a
# bare ".../jour" pattern when both would match the same text (e.g.
# "TJM : 500€/jour" only needs to match once, on the more reliable pattern).
_RANGE_PATTERNS = (_TJM_RANGE, _TAUX_JOURNALIER_RANGE, _PER_DAY_RANGE)
_SINGLE_PATTERNS = (_TJM_SINGLE, _TAUX_JOURNALIER_SINGLE, _PER_DAY_SINGLE)


def _plausible(value: int) -> bool:
    return _MIN_PLAUSIBLE_TJM <= value <= _MAX_PLAUSIBLE_TJM


def extract_daily_rate(*texts: str | None) -> tuple[int | None, int | None]:
    """(daily_rate_min, daily_rate_max) parsed from the given texts
    (typically an offer's description and salary label), or (None, None) if
    nothing plausible was found. A single value (no range stated) is
    returned as (value, value), the same convention JobOffer.salary_min/
    salary_max already use for a single-figure salary."""
    combined = " ".join(text for text in texts if text)

    for pattern in _RANGE_PATTERNS:
        match = pattern.search(combined)
        if match:
            low, high = sorted(int(group) for group in match.groups())
            if _plausible(low) and _plausible(high):
                return low, high

    for pattern in _SINGLE_PATTERNS:
        match = pattern.search(combined)
        if match:
            value = int(match.group(1))
            if _plausible(value):
                return value, value

    return None, None
