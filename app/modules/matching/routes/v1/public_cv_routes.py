"""Free CV adaptation for visitors who have no account.

A visitor gives an offer (a link we read, or the pasted text) and their CV, and
gets the CV rewritten for that offer, in the language of the offer. Nothing is
stored: the CV is read in memory, the result goes back to the page, and the
PDF is rendered from what the page sends back (so editing and downloading
never call the model again).

Because anyone can call these routes, each one is limited (see
public_quota.py for the daily allowance of `generate`), and `generate` can sit
behind a Cloudflare Turnstile challenge (see core/turnstile.py).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.dependencies import DBSession
from app.core.exceptions import AppError, BadRequestError
from app.core.logging import get_logger
from app.core.models import utcnow
from app.core.ratelimit import RateLimiter, client_ip
from app.core.turnstile import turnstile_enabled, verify_turnstile
from app.modules.cv import CandidateProfile, CvService
from app.modules.matching import cv_optimization_gateway, public_quota
from app.modules.matching.cv_optimization_models import CVOptimization
from app.modules.matching.cv_optimization_service import CVOptimizationService
from app.modules.matching.cv_pdf import (
    DEFAULT_CV_TEMPLATE,
    CvTemplate,
    build_cv_pdf,
    candidate_phone,
    cv_pdf_filename,
)
from app.modules.matching.offer_page_reader import OfferPageError, read_offer_from_url

logger = get_logger("matching.public_cv")

router = APIRouter(prefix="/public/cv-adapter", tags=["public-cv"])

MIN_OFFER_CHARS = 100
MAX_OFFER_CHARS = 20_000

# Burst limits per visitor address (the daily allowance is in public_quota.py).
# They also bound a visitor who keeps sending files that fail, since a failed
# call gives its allowance back.
_read_link_limit = RateLimiter(
    times=20, seconds=3600, scope="public_cv:link", identify=client_ip
)
_generate_limit = RateLimiter(
    times=15, seconds=3600, scope="public_cv:generate", identify=client_ip
)
_advice_limit = RateLimiter(
    times=30, seconds=3600, scope="public_cv:advice", identify=client_ip
)
_pdf_limit = RateLimiter(
    times=30, seconds=3600, scope="public_cv:pdf", identify=client_ip
)


# ---- Shapes ------------------------------------------------------------------
# Every length is capped: the PDF and advice routes take the CV back from the
# page, and anyone can call them.


class PublicBullet(BaseModel):
    text: str = Field(max_length=1500)
    status: Literal["unchanged", "modified", "added"] = "unchanged"
    original_text: str | None = Field(default=None, max_length=1500)
    why: str = Field(default="", max_length=600)
    # A sentence describing the job's mission: printed as a paragraph under the
    # job title instead of a bullet.
    context: bool = False


class PublicExperience(BaseModel):
    title: str = Field(default="", max_length=200)
    company: str = Field(default="", max_length=200)
    period: str = Field(default="", max_length=100)
    bullets: list[PublicBullet] = Field(default_factory=list, max_length=40)
    # "Technical environment" of the job, as read from the CV (not rewritten).
    tools: list[str] = Field(default_factory=list, max_length=40)


class PublicSkill(BaseModel):
    skill: str = Field(max_length=100)
    added: bool = False
    category: str = Field(default="", max_length=40)


class PublicFormation(BaseModel):
    title: str = Field(default="", max_length=250)
    school_period: str = Field(default="", max_length=250)


class PublicLanguage(BaseModel):
    name: str = Field(default="", max_length=100)
    level: str = Field(default="", max_length=100)


class PublicCertification(BaseModel):
    title: str = Field(default="", max_length=200)
    issuer_period: str = Field(default="", max_length=200)


class PublicCv(BaseModel):
    """The adapted CV, as the page shows, edits and sends it back."""

    # Who the CV is about, read from the candidate's own CV.
    first_name: str = Field(default="", max_length=100)
    last_name: str = Field(default="", max_length=100)
    email: str = Field(default="", max_length=254)
    location: str = Field(default="", max_length=100)
    phone: str = Field(default="", max_length=40)
    # Language the CV is written in: ISO 639-1 code of the offer's language.
    language: str = Field(default="", max_length=5)
    headline: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=4000)
    summary_why: str = Field(default="", max_length=1000)
    experiences: list[PublicExperience] = Field(default_factory=list, max_length=20)
    skills: list[PublicSkill] = Field(default_factory=list, max_length=100)
    formations: list[PublicFormation] = Field(default_factory=list, max_length=30)
    languages: list[PublicLanguage] = Field(default_factory=list, max_length=30)
    certifications: list[PublicCertification] = Field(
        default_factory=list, max_length=50
    )
    advice: str = Field(default="", max_length=3000)
    # Name to give the downloaded PDF. `generate` fills it; the PDF route
    # rebuilds it itself rather than trusting this.
    filename: str = Field(default="", max_length=200)


class OfferFromUrlRequest(BaseModel):
    url: str = Field(max_length=2000)


class OfferFromUrlResult(BaseModel):
    text: str
    title: str | None
    host: str
    structured: bool


class AdviceRequest(BaseModel):
    offer_text: str = Field(max_length=MAX_OFFER_CHARS)
    cv: PublicCv


class AdviceResult(BaseModel):
    advice: str


class StatusResult(BaseModel):
    enabled: bool
    daily_limit: int
    remaining: int
    # True when `generate` needs a Cloudflare Turnstile token.
    captcha_required: bool


class ClientIpResult(BaseModel):
    ip: str
    forwarded_for: str | None


# ---- Helpers -----------------------------------------------------------------


class PublicCvDisabledError(AppError):
    status_code = 503
    code = "public_cv_disabled"
    message = "L'adaptation gratuite du CV est momentanément indisponible."


def _ensure_enabled() -> None:
    if not get_settings().PUBLIC_CV_ADAPTER_ENABLED:
        raise PublicCvDisabledError()


def _check_offer_text(offer_text: str) -> str:
    text = offer_text.strip()
    if len(text) < MIN_OFFER_CHARS:
        raise BadRequestError(
            "Le texte de l'offre est trop court : colle l'annonce complète.",
            code="offer_too_short",
        )
    if len(text) > MAX_OFFER_CHARS:
        raise BadRequestError(
            "Le texte de l'offre est trop long : colle seulement l'annonce.",
            code="offer_too_long",
        )
    return text


def _profile_from_public_cv(cv: PublicCv) -> CandidateProfile:
    """A throw-away profile built from what the page sent back, only for
    `build_cv_pdf`. Never added to a session, so nothing is saved. The phone
    number goes in `raw_text` because that is where the PDF header reads it."""
    return CandidateProfile(  # type: ignore[call-arg]  # in-memory only
        first_name=cv.first_name or None,
        last_name=cv.last_name or None,
        email=cv.email or None,
        location=cv.location or None,
        headline=cv.headline or None,
        raw_text=cv.phone,
        experiences=[
            {"company": exp.company, "tools": exp.tools} for exp in cv.experiences
        ],
        formations=[f.model_dump() for f in cv.formations if f.title.strip()],
        languages=[lang.model_dump() for lang in cv.languages if lang.name.strip()],
        certifications=[c.model_dump() for c in cv.certifications if c.title.strip()],
    )


def _cv_for_advice(cv: PublicCv) -> dict:
    """The CV as the visitor edited it, in the compact shape the advice prompt
    reads: only what a recruiter would read."""
    return {
        "headline": cv.headline,
        "summary": cv.summary,
        "experiences": [
            {
                "title": exp.title,
                "company": exp.company,
                "bullets": [b.text for b in exp.bullets if b.text.strip()],
            }
            for exp in cv.experiences
        ],
        "skills": [s.skill for s in cv.skills if s.skill.strip()],
        "certifications": [
            {"title": c.title, "issuer_period": c.issuer_period}
            for c in cv.certifications
            if c.title.strip()
        ],
        "languages": [
            {"name": lang.name, "level": lang.level}
            for lang in cv.languages
            if lang.name.strip()
        ],
    }


# ---- Routes ------------------------------------------------------------------


@router.get("/status", response_model=StatusResult)
async def status(request: Request) -> StatusResult:
    """What the page needs before showing the form: is the service on, how many
    adaptations this visitor has left today, and is the anti-bot challenge on."""
    settings = get_settings()
    return StatusResult(
        enabled=settings.PUBLIC_CV_ADAPTER_ENABLED,
        daily_limit=settings.PUBLIC_CV_DAILY_PER_VISITOR,
        remaining=await public_quota.remaining_for(client_ip(request)),
        captcha_required=turnstile_enabled(),
    )


@router.get("/my-ip", response_model=ClientIpResult)
async def my_ip(request: Request) -> ClientIpResult:
    """The address the API reads for the visitor, and the raw forwarding
    header: to check, once deployed, that it is the visitor's own address
    (TRUSTED_PROXY_HOPS in the .env must match the proxies in front)."""
    return ClientIpResult(
        ip=client_ip(request), forwarded_for=request.headers.get("X-Forwarded-For")
    )


@router.post(
    "/offer-from-url",
    response_model=OfferFromUrlResult,
    dependencies=[Depends(_read_link_limit)],
)
async def read_offer_from_link(body: OfferFromUrlRequest) -> OfferFromUrlResult:
    """Fetch an offer page and return its text. No model call, nothing stored."""
    _ensure_enabled()
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


@router.post(
    "/generate",
    response_model=PublicCv,
    dependencies=[Depends(_generate_limit)],
)
async def generate_cv(
    request: Request,
    session: DBSession,
    file: UploadFile = File(...),
    offer_text: str = Form(...),
    captcha_token: str | None = Form(default=None),
) -> PublicCv:
    """Reads the CV, adapts it to the offer in the offer's language, returns it.
    Uses one of the visitor's daily adaptations (given back if it fails on our
    side)."""
    _ensure_enabled()
    ip = client_ip(request)
    await verify_turnstile(captcha_token, remote_ip=ip)
    text = _check_offer_text(offer_text)

    settings = get_settings()
    data = await file.read()
    if len(data) > settings.CV_MAX_UPLOAD_MB * 1024 * 1024:
        raise BadRequestError(
            f"Le fichier dépasse la taille maximale de {settings.CV_MAX_UPLOAD_MB} Mo.",
            code="file_too_large",
        )

    await public_quota.consume(ip)
    try:
        values, _kind = await CvService.parse_cv_file(
            filename=file.filename or "cv",
            content_type=file.content_type,
            data=data,
        )
        profile = CandidateProfile(**values)  # type: ignore[call-arg]  # in-memory
        if not profile.experiences:
            raise BadRequestError(
                "Nous n'avons trouvé aucune expérience dans ce CV : vérifie que "
                "c'est bien ton CV complet.",
                code="cv_without_experience",
            )
        adapted = await CVOptimizationService(session).generate_in_offer_language(
            profile, text
        )
    except Exception:
        # Nothing was delivered: the visitor keeps their adaptation.
        await public_quota.refund(ip)
        raise

    optimization = adapted.optimization
    experiences = []
    for index, exp in enumerate(optimization.experiences):
        original = (
            profile.experiences[index] if index < len(profile.experiences) else {}
        )
        experiences.append(
            PublicExperience.model_validate(
                {
                    **exp,
                    "tools": [
                        str(t).strip()
                        for t in (original.get("tools") or [])
                        if str(t).strip()
                    ],
                }
            )
        )
    return PublicCv(
        first_name=profile.first_name or "",
        last_name=profile.last_name or "",
        email=profile.email or "",
        location=profile.location or "",
        phone=candidate_phone(profile) or "",
        language=adapted.language,
        headline=optimization.headline,
        summary=optimization.summary,
        summary_why=optimization.summary_why,
        experiences=experiences,
        skills=[PublicSkill.model_validate(s) for s in optimization.skills],
        formations=[PublicFormation.model_validate(f) for f in adapted.formations],
        languages=[PublicLanguage.model_validate(lang) for lang in adapted.languages],
        certifications=[
            PublicCertification(
                title=str(c.get("title") or "")[:200],
                issuer_period=str(c.get("issuer_period") or "")[:200],
            )
            for c in profile.certifications or []
            if c.get("title")
        ],
        advice=optimization.advice,
        filename=cv_pdf_filename(profile, optimization.headline),
    )


@router.post(
    "/advice", response_model=AdviceResult, dependencies=[Depends(_advice_limit)]
)
async def refresh_cv_advice(body: AdviceRequest) -> AdviceResult:
    """New "Conseil" for a CV the visitor edited. One short model call, nothing
    stored: the page calls it when the visitor finishes editing."""
    _ensure_enabled()
    text = _check_offer_text(body.offer_text)
    advice = await cv_optimization_gateway.refresh_advice(_cv_for_advice(body.cv), text)
    return AdviceResult(advice=advice)


@router.post("/pdf", dependencies=[Depends(_pdf_limit)])
async def download_cv_pdf(
    body: PublicCv, template: CvTemplate = DEFAULT_CV_TEMPLATE
) -> Response:
    """Renders the CV the page holds (as edited) as a PDF. No model call."""
    _ensure_enabled()
    profile = _profile_from_public_cv(body)
    optimization = CVOptimization(  # type: ignore[call-arg]  # in-memory only
        headline=body.headline,
        summary=body.summary,
        summary_why=body.summary_why,
        experiences=[exp.model_dump(exclude={"tools"}) for exp in body.experiences],
        skills=[s.model_dump() for s in body.skills],
        advice=body.advice,
        computed_at=utcnow(),
    )
    return Response(
        content=build_cv_pdf(
            profile, optimization, template=template, lang=body.language or None
        ),
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{cv_pdf_filename(profile, body.headline)}"'
            )
        },
    )
