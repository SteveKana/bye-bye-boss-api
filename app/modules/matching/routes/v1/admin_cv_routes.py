"""Admin tool: adapt the admin's own CV to an offer pasted as plain text
(or read from a link: `offer-from-url` only fetches the text, so the admin can
check it before running the adaptation).

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
from app.modules.matching.offer_page_reader import OfferPageError, read_offer_from_url

router = APIRouter(prefix="/matching/admin", tags=["matching"])

MIN_OFFER_CHARS = 100
MAX_OFFER_CHARS = 20_000


class CvFromTextRequest(BaseModel):
    offer_text: str = Field(max_length=MAX_OFFER_CHARS)


class OfferFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)


class OfferFromUrlResult(BaseModel):
    text: str
    title: str | None
    host: str
    structured: bool


class CvFromTextResult(BaseModel):
    headline: str
    summary: str
    summary_why: str
    experiences: list[dict]
    skills: list[dict]
    advice: str
    computed_at: datetime
    # Name to give the downloaded PDF (candidate + adapted title). Filled by
    # `generate`; the PDF route rebuilds it itself rather than trusting this.
    filename: str = ""


async def _own_complete_profile(session: DBSession, user_id):
    profile = await CvService(session).get_for_user(user_id)
    if profile.status != ProfileStatus.complete.value:
        raise BadRequestError(
            "Ton profil n'est pas complet : termine-le avant d'adapter ton CV."
        )
    return profile


@router.post("/offer-from-url", response_model=OfferFromUrlResult)
async def read_offer_from_link(
    body: OfferFromUrlRequest, admin: AdminUser
) -> OfferFromUrlResult:
    """Fetch an offer page and return its text. No model call, nothing stored."""
    try:
        page = await read_offer_from_url(body.url)
    except OfferPageError as exc:
        raise BadRequestError(str(exc)) from None
    return OfferFromUrlResult(
        text=page.text,
        title=page.title,
        host=page.host,
        structured=page.used_structured_data,
    )


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
        filename=cv_pdf_filename(profile, optimization.headline),
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
            "Content-Disposition": (
                f'attachment; filename="{cv_pdf_filename(profile, body.headline)}"'
            )
        },
    )
