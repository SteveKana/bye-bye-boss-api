"""Cloudflare Turnstile check (anti-bot challenge) for routes open to anyone.

Off unless `TURNSTILE_SECRET_KEY` is set: dev and tests run without it. When it
is set, a call without a token Cloudflare accepts is refused.
"""

from __future__ import annotations

import httpx

from app.core.config import get_settings
from app.core.exceptions import BadRequestError
from app.core.logging import get_logger

logger = get_logger("app.turnstile")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def turnstile_enabled() -> bool:
    return bool(get_settings().TURNSTILE_SECRET_KEY)


async def verify_turnstile(token: str | None, *, remote_ip: str | None = None) -> None:
    """Raises BadRequestError (code `captcha_failed`) unless the token is valid.
    Does nothing when Turnstile is not configured."""
    secret = get_settings().TURNSTILE_SECRET_KEY
    if not secret:
        return
    failure = BadRequestError(
        "La vérification anti-robot a échoué. Recharge la page et réessaie.",
        code="captcha_failed",
    )
    if not token:
        raise failure
    data = {"secret": secret, "response": token}
    if remote_ip and remote_ip != "unknown":
        data["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(VERIFY_URL, data=data)
        ok = response.status_code == 200 and bool(response.json().get("success"))
    except (httpx.HTTPError, ValueError) as exc:
        # Cloudflare unreachable: refusing is the safe side (the page tells the
        # visitor to retry), and it is logged for whoever runs the site.
        logger.warning("turnstile_unreachable", error=str(exc))
        raise failure from None
    if not ok:
        raise failure
