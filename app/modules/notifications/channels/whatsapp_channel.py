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

Template constraint: Meta template messages take a small number of
*positional* variables ({{1}}, {{2}}, ...), not an arbitrary list -- there's
no way to loop over "however many offers today" inside one template. This
sends first_name/count/top offer's title+company/a link to see the rest,
and expects a template shaped to match (create one in Meta Business Manager
under WHATSAPP_TEMPLATE_NAME with that same parameter order before this can
go live).
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


async def send_brief_whatsapp(
    *,
    phone_number: str,
    first_name: str | None,
    items: list[BriefItem],
    dashboard_url: str,
    client: httpx.AsyncClient | None = None,
) -> bool:
    if not items:
        return False
    if not is_configured():
        logger.warning("whatsapp_not_configured", phone_number=phone_number)
        return False

    settings = get_settings()
    top = items[0]
    url = (
        f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/"
        f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "template",
        "template": {
            "name": settings.WHATSAPP_TEMPLATE_NAME,
            "language": {"code": "fr"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": first_name or "candidat"},
                        {"type": "text", "text": str(len(items))},
                        {"type": "text", "text": top.title},
                        {"type": "text", "text": top.company_name},
                        {"type": "text", "text": dashboard_url},
                    ],
                }
            ],
        },
    }

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=settings.WHATSAPP_TIMEOUT_SECONDS)
    try:
        response = await http.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {settings.WHATSAPP_ACCESS_TOKEN}"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("whatsapp_brief_send_failed", error=str(exc))
        return False
    finally:
        if owns_client:
            await http.aclose()
    return True
