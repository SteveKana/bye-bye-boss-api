"""Geographic pre-filter, applied to the offer pool before shortlist_offers.

A candidate's `mobility` preference (see cv/schemas.py's `Mobility` Literal)
can restrict which offers are even worth considering, before cosine
similarity or the LLM ever sees them:

  * "France entière" (or unset): no filtering -- the whole pool is eligible.
  * "Région uniquement": only offers in the candidate's chosen
    `mobility_region`, or fully remote.
  * "Ville uniquement": only offers that look like they're in the
    candidate's own free-text `location`, or fully remote.

An offer whose zone can't be determined (no `region` derived at ingestion,
or no `location` string at all) is EXCLUDED, not shown -- an explicit
product decision (2026-09-21): a candidate who restricted their search
geographically would rather miss an occasional real match than see offers
that might be nowhere near them.

This lives in `matching` (not `offers` or `cv`) because it's the one module
that already depends on both (see the module dependency graph in
app/modules/*/__init__.py) -- comparing a CandidateProfile's preference
against a JobOffer's derived région needs both, and putting it in either
side would create the cross-dependency `offers`/`cv` don't otherwise need.

This function only decides what's eligible for a *new* match -- it doesn't
touch matches that already exist. See MatchingService._run_for_profile,
which also prunes any existing CandidateMatch for an offer this excludes
(otherwise a match computed before the offer's zone was known, or before
the candidate restricted their mobility, would linger on the dashboard
forever with nothing left to ever revisit it).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.core.text import fold
from app.modules.cv import CandidateProfile
from app.modules.offers import JobOffer

_MOBILITY_UNRESTRICTED = {None, "", "France entière"}

# Tokens too generic to identify a specific place on their own -- dropped
# before comparing a profile's `location` against an offer's, so "Paris,
# France" vs. "75 - Paris" match on "paris" rather than failing to match at
# all because "france" isn't in the second string.
_NOISE_TOKENS = {"france"}


def _place_tokens(text: str | None) -> set[str]:
    folded = fold(text)
    tokens = {t for t in re.findall(r"[a-z0-9]+", folded) if len(t) >= 3}
    return tokens - _NOISE_TOKENS


def _city_matches(profile_location: str | None, offer_location: str | None) -> bool:
    """Free-text city comparison -- neither source gives a structured city
    field (see JobOffer.region's docstring for why région has one and city
    doesn't), so this is deliberately loose: any shared, non-generic word
    (ignoring accents/case) counts as a match. Missing either side means
    there's nothing to compare, so no match -- consistent with the
    exclude-when-undetermined policy above."""
    if not profile_location or not offer_location:
        return False
    return bool(_place_tokens(profile_location) & _place_tokens(offer_location))


def filter_by_geography(
    profile: CandidateProfile, offers: Sequence[JobOffer]
) -> list[JobOffer]:
    """The subset of `offers` compatible with `profile`'s mobility
    preference. Order is preserved; nothing is re-ranked here, only
    excluded -- shortlist_offers still does the ranking afterward."""
    mobility = profile.mobility
    if mobility in _MOBILITY_UNRESTRICTED:
        return list(offers)

    if mobility == "Région uniquement":
        return [
            offer
            for offer in offers
            if offer.is_full_remote
            or (offer.region is not None and offer.region == profile.mobility_region)
        ]

    if mobility == "Ville uniquement":
        return [
            offer
            for offer in offers
            if offer.is_full_remote or _city_matches(profile.location, offer.location)
        ]

    # An unrecognized mobility value (shouldn't happen -- PreferencesUpdate
    # validates against the Mobility Literal -- but a stored value predating
    # a future change to that Literal is possible) fails open rather than
    # silently hiding every offer from a candidate we can't classify.
    return list(offers)
