from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.dependencies import DBSession
from app.core.exceptions import NotFoundError
from app.modules.auth import CurrentUser
from app.modules.cv import CvService
from app.modules.matching.models import CandidateMatch
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.schemas import CandidateMatchRead
from app.modules.offers import JobOffer, JobOfferRead, JobOfferRepository

router = APIRouter(prefix="/matching", tags=["matching"])


def _to_read(match: CandidateMatch, offer: JobOffer) -> CandidateMatchRead:
    return CandidateMatchRead(
        id=match.id,
        company_name=match.company_name,
        career_score=match.career_score,
        ats_score=match.ats_score,
        ats_potential=match.ats_potential,
        blocking_message=match.blocking_message,
        analysis=match.analysis,
        regret_availability=match.regret_availability,
        regret_score=match.regret_score,
        computed_at=match.computed_at,
        offer=JobOfferRead.model_validate(offer),
    )


@router.get("/top", response_model=list[CandidateMatchRead])
async def top_matches(
    session: DBSession, user: CurrentUser
) -> list[CandidateMatchRead]:
    """Pre-computed matches for the current user, best career_score first.

    Nothing is computed here -- see the `matching` module's docstring for
    why scoring runs entirely in the background. A profile that hasn't been
    matched yet (too new, or not "complete") simply has no rows here yet.
    """
    profile = await CvService(session).get_for_user(user.id)
    matches = await CandidateMatchRepository(session).list_top_for_profile(profile.id)

    offers = JobOfferRepository(session)
    results: list[CandidateMatchRead] = []
    for match in matches:
        offer = await offers.get(match.job_offer_id)
        if offer is None:
            # The offer was removed from the pool since this match was
            # computed -- skip rather than show a match with no offer to link to.
            continue
        results.append(_to_read(match, offer))
    return results


@router.get("/{match_id}", response_model=CandidateMatchRead)
async def get_match(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CandidateMatchRead:
    """One pre-computed match, in full -- backs the "Opportunité" detail
    page (scores, matched skills, ATS gaps, actions...), which /top's
    summary shape doesn't need to carry for every row in the dashboard list.

    A match that exists but doesn't belong to the current user's profile
    returns 404, not 403 -- see core/exceptions.py's anti-IDOR convention:
    never confirm to a caller that a given match_id exists for someone else.
    """
    profile = await CvService(session).get_for_user(user.id)
    match = await CandidateMatchRepository(session).get(match_id)
    if match is None or match.candidate_profile_id != profile.id:
        raise NotFoundError("Correspondance introuvable.")

    offer = await JobOfferRepository(session).get(match.job_offer_id)
    if offer is None:
        raise NotFoundError("Correspondance introuvable.")

    return _to_read(match, offer)
