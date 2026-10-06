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

v2's raw cosine similarity fixed that, but production (2026-09-21) turned up
a subtler version of the same failure: Adzuna's free-tier API truncates
descriptions to ~500 characters against France Travail's ~2500, so Adzuna
offers embed with systematically less semantic signal and score lower
across the board (measured: mean cosine similarity 0.49 vs 0.58 for one
real profile) -- not because they're less relevant, but because there's
simply less text to encode. Comparing raw scores directly meant Adzuna
could again go entirely unshortlisted. Each offer's score is now expressed
relative to its own source's mean/stdev (a z-score) before ranking, so a
standout offer from a "thinner-content" source can compete with an average
offer from a richer one -- without reintroducing v1's bug: a mediocre offer
is still a mediocre z-score regardless of source, nothing is boosted just
for where it came from.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
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


def _normalize_per_source(
    scored: list[tuple[float, JobOffer]],
) -> list[tuple[float, JobOffer]]:
    """Re-express each raw score as a z-score against its own source's
    mean/stdev, so sources whose *content* differs systematically (not
    their relevance) aren't compared on the same absolute scale -- see this
    module's docstring. A source with a single offer, or whose offers are
    all tied, has zero variance; that offer's z-score is 0 (average for its
    source) rather than a division by zero."""
    by_source: dict[str, list[float]] = defaultdict(list)
    for score, offer in scored:
        by_source[offer.source].append(score)

    baselines = {
        source: (statistics.fmean(values), statistics.pstdev(values))
        for source, values in by_source.items()
    }

    normalized = []
    for score, offer in scored:
        mean, stdev = baselines[offer.source]
        z = (score - mean) / stdev if stdev > 0 else 0.0
        normalized.append((z, offer))
    return normalized


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
    Offers with no embedding are excluded from the per-source baseline
    below (they'd skew it) and always sort after every embedded offer,
    whatever their source.
    """
    if not profile.embedding:
        return []

    with_embedding = [offer for offer in offers if offer.embedding]
    without_embedding = [offer for offer in offers if not offer.embedding]

    scored = [
        (_cosine_similarity(profile.embedding, offer.embedding), offer)
        for offer in with_embedding
        if offer.embedding
    ]
    normalized = _normalize_per_source(scored)
    normalized.sort(key=lambda pair: pair[0], reverse=True)

    ranked = [offer for _, offer in normalized] + without_embedding
    return ranked[:limit]
