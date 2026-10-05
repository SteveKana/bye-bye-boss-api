"""Seniority rules shared by the matching pipeline (Steve, 2026-10-05).

A candidate with years of experience must not be offered an internship or a
junior post, and the Career Score of an offer asking for a very different
number of years than the candidate has is lowered. Three pure, I/O-free
helpers carry the rules:

* `candidate_years` / `is_student_profile` -- who the candidate is.
* `is_entry_level_contract` / `is_junior_offer` -- what the offer is.
* `required_years_range` + `career_score_penalty` -- how far the offer's
  requested experience is from the candidate's.

Like core/remote_work.py and core/contract_type.py these are keyword
heuristics: a wording they do not recognise is a miss (nothing is filtered,
nothing is penalised), never a wrong exclusion on its own.

Student rule (Steve): at most STUDENT_MAX_YEARS years of experience AND stages
/alternances on the CV. A CV listing no experience at all is a beginner too
(otherwise a fresh graduate would never see an internship); a candidate whose
experience cannot be read is left alone (no filtering).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

STUDENT_MAX_YEARS = 2.0
# Junior posts are dropped from this many years of experience onwards.
JUNIOR_EXCLUDED_FROM_YEARS = 3.0

# Career Score window: an offer asking for N years is fine when
# years - WINDOW_BELOW <= N <= years + WINDOW_ABOVE.
WINDOW_BELOW = 1.0
WINDOW_ABOVE = 3.0
POINTS_PER_YEAR_BELOW = 5
POINTS_PER_YEAR_ABOVE = 8
MAX_PENALTY_BELOW = 30
MAX_PENALTY_ABOVE = 40

_MAX_REASONABLE_YEARS = 40.0

_YEARS_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*\+?\s*(?:ans?|years?|yrs?)\b", re.I)
_MONTHS_RE = re.compile(r"(\d+)\s*mois\b", re.I)

_ENTRY_CONTRACT_RE = re.compile(
    r"\bstages?\b|\bstagiaires?\b|\binternships?\b|\balternance\b|\balternants?\b|"
    r"\bapprentissage\b|\bapprentis?\b|professionnalisation",
    re.I,
)
_JUNIOR_RE = re.compile(r"\bjuniors?\b|\bjr\b|\bd[ée]butants?\b", re.I)

_SEP = r"(?:\s*(?:à|a|-|–|/|to|ou)\s*)"
_NUM = r"(\d{1,2})"
# "3 à 5 ans d'expérience", "5 ans d'expérience", "5+ ans d'expérience"
_REQ_BEFORE_RE = re.compile(
    rf"{_NUM}(?:{_SEP}{_NUM})?\s*\+?\s*(?:ans?|years?)\s*(?:minimum\s*)?"
    rf"(?:d['’]?\s*|of\s+)?(?:exp[ée]riences?|experience)",
    re.I,
)
# "expérience de 5 ans", "minimum 3 ans", "au moins 5 ans", "justifiez de 3 ans"
_REQ_AFTER_RE = re.compile(
    rf"(?:exp[ée]riences?|experience)\s*(?:professionnelle\s*)?"
    rf"(?:de|d['’]|of|:|-)?\s*(?:minimum|au moins|plus de|at least)?\s*"
    rf"{_NUM}(?:{_SEP}{_NUM})?\s*\+?\s*(?:ans?|years?)\b|"
    rf"(?:minimum|au moins|at least|justifi\w+\s+de)\s*{_NUM}"
    rf"(?:{_SEP}{_NUM})?\s*\+?\s*(?:ans?|years?)\b",
    re.I,
)


def candidate_years(total_experience: str | None) -> float | None:
    """Years of experience read from the CV's free-text total ("7 ans",
    "3 ans et 6 mois", "8 mois"), or None when it cannot be read."""
    if not total_experience:
        return None
    text = total_experience.strip()
    years_match = _YEARS_RE.search(text)
    months_match = _MONTHS_RE.search(text)
    if years_match is None and months_match is None:
        return None
    total = 0.0
    if years_match:
        total += float(years_match.group(1).replace(",", "."))
    if months_match:
        total += int(months_match.group(1)) / 12
    return total if total <= _MAX_REASONABLE_YEARS else None


def _experience_texts(experiences: Iterable[Mapping[str, Any]] | None) -> list[str]:
    texts = []
    for exp in experiences or []:
        if isinstance(exp, Mapping):
            texts.append(f"{exp.get('title') or ''} {exp.get('company') or ''}")
    return texts


def is_student_profile(
    total_experience: str | None,
    experiences: Iterable[Mapping[str, Any]] | None,
) -> bool:
    """Steve's rule: <= 2 years of experience AND stages/alternances on the
    CV. A CV with no experience listed at all is a beginner as well."""
    years = candidate_years(total_experience)
    experiences = list(experiences or [])
    if years is None:
        # Unreadable total: only an empty experience list tells us anything.
        return not experiences and not total_experience
    if years > STUDENT_MAX_YEARS:
        return False
    if not experiences:
        return True
    return any(
        _ENTRY_CONTRACT_RE.search(text) for text in _experience_texts(experiences)
    )


def is_entry_level_contract(contract_type: str | None, title: str | None) -> bool:
    """Stage / Alternance (apprentissage, professionnalisation)."""
    return bool(_ENTRY_CONTRACT_RE.search(f"{contract_type or ''} {title or ''}"))


def is_junior_offer(title: str | None) -> bool:
    """ "Junior" / "Débutant" in the title. The description is deliberately not
    read: "débutant accepté" there is common on offers open to everyone."""
    return bool(_JUNIOR_RE.search(title or ""))


def is_excluded_for_candidate(
    *,
    total_experience: str | None,
    experiences: Iterable[Mapping[str, Any]] | None,
    contract_type: str | None,
    title: str | None,
) -> bool:
    """True when the offer must never be proposed to this candidate:
    stage/alternance for anyone but a student, junior from 3 years on.
    Unknown experience -> nothing is excluded."""
    years = candidate_years(total_experience)
    if years is None:
        return False
    if is_student_profile(total_experience, experiences):
        return False
    if is_entry_level_contract(contract_type, title):
        return True
    return years >= JUNIOR_EXCLUDED_FROM_YEARS and is_junior_offer(title)


def required_years_range(*texts: str | None) -> tuple[float, float] | None:
    """The experience the offer asks for, as (lowest, highest) years -- the
    largest figure mentioned, so "5 ans en Java, 2 ans en AWS" reads 5.
    None when the offer states nothing."""
    found: list[tuple[float, float]] = []
    for text in texts:
        if not text:
            continue
        for pattern in (_REQ_BEFORE_RE, _REQ_AFTER_RE):
            for match in pattern.finditer(text):
                numbers = [float(g) for g in match.groups() if g]
                if not numbers:
                    continue
                low, high = min(numbers), max(numbers)
                if high <= _MAX_REASONABLE_YEARS:
                    found.append((low, high))
    if not found:
        return None
    return max(found, key=lambda pair: (pair[1], pair[0]))


def career_score_penalty(
    years: float | None, required: tuple[float, float] | None
) -> int:
    """Points to take off the Career Score (0 = none).

    No change while the requested years fall inside [years - 1, years + 3];
    below: 5 points per missing year (max 30); above: 8 points per year
    beyond (max 40). An offer silent on years, or an unknown candidate: 0.
    A range ("3 à 5 ans") counts as inside as soon as it touches the window.
    """
    if years is None or required is None:
        return 0
    low, high = required
    window_low = years - WINDOW_BELOW
    window_high = years + WINDOW_ABOVE
    if high < window_low:
        gap = window_low - high
        return min(MAX_PENALTY_BELOW, round(gap * POINTS_PER_YEAR_BELOW))
    if low > window_high:
        gap = low - window_high
        return min(MAX_PENALTY_ABOVE, round(gap * POINTS_PER_YEAR_ABOVE))
    return 0
