from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.dependencies import DBSession
from app.core.exceptions import NotFoundError
from app.modules.auth import CurrentUser
from app.modules.cv import CvService
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_service import CVOptimizationService
from app.modules.matching.routes.v1.matching_routes import _get_owned_match_or_404
from app.modules.matching.schemas import CVOptimizationRead
from app.modules.offers import JobOfferRepository

router = APIRouter(prefix="/matching", tags=["matching"])

_NO_OPTIMIZATION_YET = "Aucune optimisation de CV pour cette offre pour le moment."


def _to_read(
    optimization: CVOptimization, *, ats_score_before: int, ats_score_after: int
) -> CVOptimizationRead:
    return CVOptimizationRead(
        id=optimization.id,
        headline=optimization.headline,
        summary=optimization.summary,
        summary_why=optimization.summary_why,
        experiences=optimization.experiences,
        skills=optimization.skills,
        advice=optimization.advice,
        computed_at=optimization.computed_at,
        confirmed_at=optimization.confirmed_at,
        ats_score_before=ats_score_before,
        ats_score_after=ats_score_after,
    )


@router.post("/{match_id}/cv-optimization", response_model=CVOptimizationRead)
async def generate_cv_optimization(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CVOptimizationRead:
    """Backs "📄 Adapter mon CV pour cette offre". Generates the optimization
    on first call for this match (a real, ~10-30s LLM call) and simply
    returns the cached result on every later call -- see
    CVOptimizationService's docstring for why this runs synchronously in the
    request rather than as a background job, and why there's no regenerate
    path yet.
    """
    match = await _get_owned_match_or_404(match_id, session, user)
    profile = await CvService(session).get_for_user(user.id)
    offer = await JobOfferRepository(session).get(match.job_offer_id)
    if offer is None:
        raise NotFoundError("Correspondance introuvable.")

    optimization = await CVOptimizationService(session).get_or_generate(
        match, profile, offer
    )
    return _to_read(
        optimization,
        ats_score_before=match.ats_score,
        ats_score_after=match.ats_potential,
    )


@router.get("/{match_id}/cv-optimization", response_model=CVOptimizationRead)
async def get_cv_optimization(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CVOptimizationRead:
    """The cached optimization for this match, if one was already generated
    -- 404 if the candidate hasn't clicked "Adapter mon CV" for this offer
    yet (the frontend falls back to POSTing to generate it in that case)."""
    match = await _get_owned_match_or_404(match_id, session, user)
    optimization = await CVOptimizationService(session).optimizations.get_by_match(
        match.id
    )
    if optimization is None:
        raise NotFoundError(_NO_OPTIMIZATION_YET)
    return _to_read(
        optimization,
        ats_score_before=match.ats_score,
        ats_score_after=match.ats_potential,
    )


@router.post("/{match_id}/cv-optimization/confirm", response_model=CVOptimizationRead)
async def confirm_cv_optimization(
    match_id: uuid.UUID, session: DBSession, user: CurrentUser
) -> CVOptimizationRead:
    """ "Créer cette variante de CV" -- see CVOptimization's docstring for
    exactly what this does and doesn't do today (records intent, no file is
    produced yet)."""
    match = await _get_owned_match_or_404(match_id, session, user)
    service = CVOptimizationService(session)
    optimization = await service.optimizations.get_by_match(match.id)
    if optimization is None:
        raise NotFoundError(_NO_OPTIMIZATION_YET)
    optimization = await service.confirm(optimization)
    return _to_read(
        optimization,
        ats_score_before=match.ats_score,
        ats_score_after=match.ats_potential,
    )
