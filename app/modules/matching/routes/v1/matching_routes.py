from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DBSession
from app.modules.auth import CurrentUser
from app.modules.cv import CvService
from app.modules.matching.repository import CandidateMatchRepository
from app.modules.matching.schemas import CandidateMatchRead
from app.modules.offers import JobOfferRead, JobOfferRepository

router = APIRouter(prefix="/matching", tags=["matching"])


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
        results.append(
            CandidateMatchRead(
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
        )
    return results
