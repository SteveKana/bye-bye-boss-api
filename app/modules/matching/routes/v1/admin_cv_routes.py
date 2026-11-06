"""Admin tool: adapt the admin's own CV to an offer pasted as plain text.

Nothing is stored. `generate` calls the model (the same engine as "Adapter mon
CV pour cette offre", but with no match behind it) and returns the result;
`pdf` renders a result the page sends back, so downloading never calls the
model again.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from app.core.dependencies import DBSession
from app.core.exceptions import BadRequestError
from app.core.models import utcnow
from app.modules.auth import AdminUser
from app.modules.cv import CvService, ProfileStatus
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_service import CVOptimizationService
from app.modules.matching.cv_pdf import (
    DEFAULT_CV_TEMPLATE,
    CvTemplate,
    build_cv_pdf,
    cv_pdf_filename,
)

router = APIRouter(prefix="/matching/admin", tags=["matching"])

MIN_OFFER_CHARS = 100
MAX_OFFER_CHARS = 20_000


class CvFromTextRequest(BaseModel):
    offer_text: str = Field(max_length=MAX_OFFER_CHARS)


class CvFromTextResult(BaseModel):
    headline: str
    summary: str
    summary_why: str
    experiences: list[dict]
    skills: list[dict]
    advice: str
    computed_at: datetime


async def _own_complete_profile(session: DBSession, user_id):
    profile = await CvService(session).get_for_user(user_id)
    if profile.status != ProfileStatus.complete.value:
        raise BadRequestError(
            "Ton profil n'est pas complet : termine-le avant d'adapter ton CV."
        )
    return profile


@router.post("/cv-from-offer-text", response_model=CvFromTextResult)
async def generate_cv_from_offer_text(
    body: CvFromTextRequest, session: DBSession, admin: AdminUser
) -> CvFromTextResult:
    text = body.offer_text.strip()
    if len(text) < MIN_OFFER_CHARS:
        raise BadRequestError(
            "Le texte de l'offre est trop court : colle l'annonce complète."
        )
    profile = await _own_complete_profile(session, admin.id)
    optimization = await CVOptimizationService(session).generate_from_text(
        profile, text
    )
    return CvFromTextResult(
        headline=optimization.headline,
        summary=optimization.summary,
        summary_why=optimization.summary_why,
        experiences=optimization.experiences,
        skills=optimization.skills,
        advice=optimization.advice,
        computed_at=optimization.computed_at,
    )


@router.post("/cv-from-offer-text/pdf")
async def download_cv_from_offer_text(
    body: CvFromTextResult,
    session: DBSession,
    admin: AdminUser,
    template: CvTemplate = DEFAULT_CV_TEMPLATE,
) -> Response:
    profile = await _own_complete_profile(session, admin.id)
    optimization = CVOptimization(  # type: ignore[call-arg]  # in-memory only
        headline=body.headline,
        summary=body.summary,
        summary_why=body.summary_why,
        experiences=body.experiences,
        skills=body.skills,
        advice=body.advice,
        computed_at=body.computed_at or utcnow(),
    )
    return Response(
        content=build_cv_pdf(profile, optimization, template=template),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{cv_pdf_filename(profile)}"'
        },
    )
