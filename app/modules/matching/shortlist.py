"""Cheap pre-filter run before any LLM call.

Scoring every offer in the pool against every candidate with the LLM would
scale cost with (candidates x offers) -- this narrows that down to the
offers actually worth spending an LLM call on.

v2: ranks by cosine similarity between the candidate's CV embedding and
each offer's embedding (see core/embeddings.py), computed ahead of time
and cached on CandidateProfile.embedding / JobOffer.embedding -- this
function itself does no I/O, just arithmetic over already-computed vectors,
so it stays a fast, pure, synchronous, fully unit-testable step.

v1 used a keyword-overlap heuristic (substring matching over CV-extracted
keywords) instead. It was replaced after a real production case: it counts
*how many* distinct keywords appear anywhere in an offer's title+description,
with no regard for meaning -- so a long, template-style listing that
enumerates every tool in its "environnement technique" section (common in
ESN/staffing-agency ads) racks up a much higher score than a shorter,
plainly-written posting that is a genuinely better fit. In production this
starved out an entire offer source (Adzuna) from ever being shortlisted for
any candidate, purely because France Travail's postings happened to be more
keyword-dense -- not because they were more relevant.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.modules.cv import CandidateProfile
from app.modules.offers import JobOffer


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def shortlist_offers(
    profile: CandidateProfile, offers: Sequence[JobOffer], *, limit: int
) -> list[JobOffer]:
    """The `limit` most relevant offers for this profile, most relevant first.

    Returns nothing if the profile has no embedding yet (matching hasn't
    run for it since its CV was last (re)imported -- see
    MatchingService._run_for_profile, which computes and caches it lazily
    before calling this). An offer with no embedding of its own (ingested
    before this feature shipped and not yet refreshed, or a past embedding
    API failure) sorts last rather than being dropped outright -- the LLM
    scoring step is the real judge of fit; this is only a cheap pre-filter,
    not the last word, so a missing signal shouldn't hide an offer forever.
    """
    if not profile.embedding:
        return []

    scored = [
        (
            _cosine_similarity(profile.embedding, offer.embedding)
            if offer.embedding
            else -1.0,
            offer,
        )
        for offer in offers
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [offer for _, offer in scored[:limit]]
