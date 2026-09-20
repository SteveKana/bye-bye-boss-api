"""Cheap pre-filter run before any LLM call.

Scoring every offer in the pool against every candidate with the LLM would
scale cost with (candidates x offers) -- this heuristic narrows that down to
the offers actually worth spending an LLM call on, using nothing more
expensive than substring matching over already-extracted CV data (headline,
identified_roles, domains, skills -- see app/modules/cv/gateway.py's
extraction prompt).

This is deliberately simple for v1: keyword overlap, no semantic similarity.
It will occasionally miss a genuine match phrased very differently from the
candidate's own wording (the LLM step itself does the semantic matching, but
only for offers that make it past this filter). A future refinement could
replace this with embedding similarity without changing anything downstream
-- MatchingService only depends on `shortlist_offers`'s signature.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.modules.cv import CandidateProfile
from app.modules.offers import JobOffer

_MIN_KEYWORD_LENGTH = 3


def _profile_keywords(profile: CandidateProfile) -> set[str]:
    values: list[str] = list(profile.identified_roles or [])
    values += list(profile.domains or [])
    values += [str(s) for s in (profile.skills or [])]
    if profile.headline:
        values.append(profile.headline)

    keywords = set()
    for value in values:
        text = str(value).strip().lower()
        if len(text) >= _MIN_KEYWORD_LENGTH:
            keywords.add(text)
    return keywords


def shortlist_offers(
    profile: CandidateProfile, offers: Sequence[JobOffer], *, limit: int
) -> list[JobOffer]:
    """The `limit` most relevant offers for this profile, most relevant first.

    An offer with zero keyword overlap is excluded entirely, even if fewer
    than `limit` offers remain -- better to score nothing than to spend an
    LLM call scoring a candidate against a clearly unrelated offer.
    """
    keywords = _profile_keywords(profile)
    if not keywords:
        return []

    scored: list[tuple[int, JobOffer]] = []
    for offer in offers:
        haystack = f"{offer.title} {offer.description or ''}".lower()
        overlap = sum(1 for keyword in keywords if keyword in haystack)
        if overlap > 0:
            scored.append((overlap, offer))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [offer for _, offer in scored[:limit]]
