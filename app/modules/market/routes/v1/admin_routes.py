"""Admin-only: preview the market-watch digest of one profile and send it."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DBSession
from app.core.exceptions import BadRequestError, NotFoundError
from app.modules.auth import AdminUser, AuthGateway
from app.modules.cv import CandidateProfile, CandidateProfileRepository, ProfileStatus
from app.modules.mailer import MailerGateway
from app.modules.market.render import render_digest
from app.modules.market.schemas import DigestPreview, DigestRequest, DigestSent
from app.modules.market.service import DigestUnavailableError, MarketDigestService

router = APIRouter(prefix="/market/admin", tags=["market"])


async def _complete_profile(session: DBSession, user_id) -> CandidateProfile:
    profile = await CandidateProfileRepository(session).get_by_user(user_id)
    if profile is None or profile.status != ProfileStatus.complete.value:
        raise NotFoundError("Ce compte n'a pas de profil complet.")
    return profile


@router.post("/digest/preview", response_model=DigestPreview)
async def preview(
    data: DigestRequest, session: DBSession, _: AdminUser
) -> DigestPreview:
    profile = await _complete_profile(session, data.user_id)
    try:
        result = await MarketDigestService(session).build(
            profile, ignore_threshold=data.ignore_threshold
        )
    except DigestUnavailableError as exc:
        return DigestPreview(ready=False, reason=str(exc))
    mail = render_digest(result.data)
    return DigestPreview(
        ready=True,
        subject=mail.subject,
        html=mail.html or "",
        label=result.data.label,
        offers_analysed=result.offers_analysed,
        min_offers=result.min_offers,
        below_threshold=result.below_threshold,
    )


@router.post("/digest/send", response_model=DigestSent)
async def send(data: DigestRequest, session: DBSession, _: AdminUser) -> DigestSent:
    profile = await _complete_profile(session, data.user_id)
    user = await AuthGateway(session).get_user(data.user_id)
    if user is None:
        raise NotFoundError("Compte introuvable.")
    try:
        result = await MarketDigestService(session).build(
            profile, ignore_threshold=data.ignore_threshold
        )
    except DigestUnavailableError as exc:
        raise BadRequestError(str(exc), code="digest_unavailable") from exc
    mail = render_digest(result.data)
    await MailerGateway(session).enqueue(
        to_email=user.email, subject=mail.subject, text=mail.text, html=mail.html
    )
    await session.commit()
    return DigestSent(detail=f"Veille marché « {result.data.label} » envoyée.")
