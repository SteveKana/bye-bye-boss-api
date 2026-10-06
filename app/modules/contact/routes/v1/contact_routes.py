from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.dependencies import DBSession
from app.core.ratelimit import RateLimiter
from app.modules.contact.schemas import ContactCreate, ContactResponse
from app.modules.contact.service import ContactService

router = APIRouter(prefix="/contact", tags=["contact"])

# Public form: a handful of messages per visitor and per 10 minutes is plenty.
send_limit = RateLimiter(times=5, seconds=600, scope="contact:send")

_MESSAGES = {
    "fr": (
        "Merci, votre message a bien été envoyé. Nous vous répondons dès que possible."
    ),
    "en": (
        "Thanks, your message has been sent. We'll get back to you as soon as possible."
    ),
}


@router.post(
    "",
    response_model=ContactResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(send_limit)],
)
async def send_message(data: ContactCreate, session: DBSession) -> ContactResponse:
    await ContactService(session).send(data)
    return ContactResponse(detail=_MESSAGES.get(data.locale, _MESSAGES["fr"]))
