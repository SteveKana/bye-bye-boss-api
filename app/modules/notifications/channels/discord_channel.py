"""Discord channel -- a per-user webhook URL, not a bot.

Deliberately the simplest integration that actually works without us
running any Discord infrastructure: the candidate creates a webhook
themselves on a channel/DM they control (Discord: channel Settings ->
Integrations -> Webhooks -> New Webhook -> Copy URL) and pastes that URL
into their notification preferences. A real bot (OAuth install flow, a
server presence, slash commands) would need Discord Developer Portal setup
and ongoing hosting on our side for very little gain over this -- a
webhook is just an HTTP endpoint Discord gives out for free, good enough
for a one-way daily brief.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger
from app.modules.notifications.brief_item import BriefItem

logger = get_logger("notifications.discord")

_EMBED_COLOR = 0x5B3FE8  # brand purple, same hex as the frontend/PDF


def _build_payload(items: list[BriefItem]) -> dict:
    return {
        "embeds": [
            {
                "title": "📄 Votre brief du jour -- Bye Bye Boss",
                "color": _EMBED_COLOR,
                "fields": [
                    {
                        "name": f"{item.career_score}/100 · {item.company_name}",
                        "value": f"[{item.title}]({item.url})",
                        "inline": False,
                    }
                    for item in items
                ],
            }
        ]
    }


async def send_brief_discord(
    *,
    webhook_url: str,
    items: list[BriefItem],
    client: httpx.AsyncClient | None = None,
) -> bool:
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        response = await http.post(webhook_url, json=_build_payload(items))
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("discord_brief_send_failed", error=str(exc))
        return False
    finally:
        if owns_client:
            await http.aclose()
    return True
