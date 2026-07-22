from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.dependencies import DBSession
from app.core.ratelimit import RateLimiter
from app.modules.auth import AdminUser
from app.modules.leads.schemas import LeadCaptureResponse, LeadCreate, LeadRead
from app.modules.leads.service import LeadService

router = APIRouter(prefix="/leads", tags=["leads"])

# Public endpoint on a marketing page: throttle to limit spam signups.
capture_limit = RateLimiter(times=5, seconds=60, scope="leads:capture")

_MESSAGES = {
    "fr": "Merci, votre inscription est bien enregistrée.",
    "en": "Thanks, you're on the list.",
}


@router.post(
    "",
    response_model=LeadCaptureResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(capture_limit)],
)
async def capture_lead(data: LeadCreate, session: DBSession) -> LeadCaptureResponse:
    await LeadService(session).capture(data)
    return LeadCaptureResponse(detail=_MESSAGES.get(data.locale, _MESSAGES["fr"]))


@router.get("", response_model=list[LeadRead])
async def list_leads(session: DBSession, _: AdminUser) -> list[LeadRead]:
    """Admin-only export of the waitlist."""
    leads = await LeadService(session).list_all()
    return [LeadRead.model_validate(lead) for lead in leads]
