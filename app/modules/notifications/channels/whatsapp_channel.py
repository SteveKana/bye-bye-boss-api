"""WhatsApp channel -- Meta's WhatsApp Business Cloud API.

Unlike Discord (a webhook the candidate creates in two clicks) or email
(already fully wired), this one needs real setup on Bye Bye Boss's side
before it can send a single message, and there's no way around that: Meta
requires (1) a verified WhatsApp Business Account, (2) a phone number
registered to it, and (3) for any business-initiated message like a daily
brief (as opposed to a reply within 24h of the user messaging first), a
message *template* pre-approved by Meta -- free-form text isn't allowed
for this kind of outbound notification.

Until WHATSAPP_ACCESS_TOKEN/WHATSAPP_PHONE_NUMBER_ID/WHATSAPP_TEMPLATE_NAME
are set, this channel just logs and no-ops -- same "skip an unconfigured
integration rather than crash the run" convention as the offers module's
providers (see OfferProvider.is_configured).

Template constraint: a Meta template message takes a *fixed* set of
variables -- there's no way to loop over "however many offers today"
inside a single message. So instead of one message summarizing the brief,
this sends one template message *per offer* in `items`, each with that
offer's own title/company/score/link -- a candidate with 3 new matches
today gets 3 separate WhatsApp messages, each linking straight to one real
offer.

The pre-approved template (WHATSAPP_TEMPLATE_NAME, "job_offer_alert" in
Meta Business Manager) uses NAMED body variables, not positional ones --
its parameter_format is "NAMED" (confirmed via GET .../message_templates),
so every parameter sent below must carry a "parameter_name" matching the
template's own placeholder name, or Meta rejects the send with "(#100)
Invalid parameter" / "Parameter name is missing or empty". The template
has exactly 5 named body variables, in this order: first_name, job_title,
company_name, match_score, offer_link.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.notifications.brief_item import BriefItem

logger = get_logger("notifications.whatsapp")


def is_configured() -> bool:
    settings = get_settings()
    return bool(
        settings.WHATSAPP_ACCESS_TOKEN
        and settings.WHATSAPP_PHONE_NUMBER_ID
        and settings.WHATSAPP_TEMPLATE_NAME
    )


def _build_payload(
    *, phone_number: str, first_name: str | None, template_name: str, item: BriefItem
) -> dict:
    return {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "fr"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {
                            "type": "text",
                            "parameter_name": "first_name",
                            "text": first_name or "candidat",
                        },
                        {
                            "type": "text",
                            "parameter_name": "job_title",
                            "text": item.title,
                        },
                        {
                            "type": "text",
                            "parameter_name": "company_name",
                            "text": item.company_name,
                        },
                        {
                            "type": "text",
                            "parameter_name": "match_score",
                            "text": str(item.career_score),
                        },
                        {
                            "type": "text",
                            "parameter_name": "offer_link",
                            "text": item.url,
                        },
                    ],
                }
            ],
        },
    }


async def send_brief_whatsapp(
    *,
    phone_number: str,
    first_name: str | None,
    items: list[BriefItem],
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Sends one WhatsApp template message per item in `items` (see the
    module docstring for why one message per offer, not one message for
    the whole brief). Returns True as soon as at least one of those
    messages actually goes out -- a candidate still gets *some* of their
    brief on WhatsApp even if one particular send fails, so this isn't an
    all-or-nothing result. Every individual failure is logged with the
    match it was for.
    """
    if not items:
        return False
    if not is_configured():
        logger.warning("whatsapp_not_configured", phone_number=phone_number)
        return False

    settings = get_settings()
    url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/"
        f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    assert settings.WHATSAPP_TEMPLATE_NAME is not None  # guaranteed by is_configured()

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.WHATSAPP_TIMEOUT_SECONDS)
    sent_any = False
    try:
        for item in items:
            payload = _build_payload(
                phone_number=phone_number,
                first_name=first_name,
                template_name=settings.WHATSAPP_TEMPLATE_NAME,
                item=item,
            )
            try:
                response = await http.post(
                    url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"
                    },
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning(
                    "whatsapp_brief_send_failed",
                    error=str(exc),
                    match_id=item.match_id,
                )
                continue
            sent_any = True
    finally:
        if owns_client:
            await http.aclose()
    return sent_any
