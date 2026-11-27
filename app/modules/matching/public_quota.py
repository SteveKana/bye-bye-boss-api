"""Daily allowance of the free, no-account CV adaptation.

Three limits, all counted over 24 h from the first use (see
`PUBLIC_CV_*` in core/config.py):

- per visitor (IP address): `PUBLIC_CV_DAILY_PER_VISITOR` adaptations;
- for the whole site: `PUBLIC_CV_DAILY_GLOBAL_CAP`, so the model bill has a
  ceiling whatever an attacker does with many addresses; an "alert" incident
  is raised once when `PUBLIC_CV_CAP_ALERT_RATIO` of it is reached;
- (elsewhere) an hourly burst limit per address and an optional Turnstile
  challenge.

A call that fails on our side gives its use back (`refund`).
"""

from __future__ import annotations

from app.core import ratelimit
from app.core.config import get_settings
from app.core.exceptions import TooManyRequestsError
from app.core.logging import get_logger
from app.core.monitoring_events import report_incident

logger = get_logger("matching.public_quota")

WINDOW_SECONDS = 24 * 3600
_GLOBAL_KEY = "pubcv:global"


def _visitor_key(ip: str) -> str:
    return f"pubcv:ip:{ip}"


async def remaining_for(ip: str) -> int:
    """Adaptations this visitor can still run today (never negative)."""
    used = await ratelimit.backend.peek(_visitor_key(ip))
    return max(get_settings().PUBLIC_CV_DAILY_PER_VISITOR - used, 0)


async def consume(ip: str) -> None:
    """Uses one adaptation for this visitor, or raises 429 when the visitor or
    the whole site has none left."""
    settings = get_settings()
    visitor_key = _visitor_key(ip)

    count, reset = await ratelimit.backend.hit(visitor_key, WINDOW_SECONDS)
    if count > settings.PUBLIC_CV_DAILY_PER_VISITOR:
        await ratelimit.backend.refund(visitor_key)  # a refused call is not a use
        raise TooManyRequestsError(
            "Tu as utilisé tes adaptations gratuites du jour. Reviens demain, "
            "ou crée un compte gratuit pour continuer.",
            code="public_cv_daily_limit",
            details={"retry_after": reset},
            headers={"Retry-After": str(reset)},
        )

    total, total_reset = await ratelimit.backend.hit(_GLOBAL_KEY, WINDOW_SECONDS)
    cap = settings.PUBLIC_CV_DAILY_GLOBAL_CAP
    if total > cap:
        await ratelimit.backend.refund(_GLOBAL_KEY)
        await ratelimit.backend.refund(visitor_key)
        raise TooManyRequestsError(
            "Beaucoup de monde aujourd'hui : le service gratuit est complet. "
            "Reviens demain, ou crée un compte gratuit.",
            code="public_cv_capacity",
            details={"retry_after": total_reset},
            headers={"Retry-After": str(total_reset)},
        )
    alert_at = max(int(cap * settings.PUBLIC_CV_CAP_ALERT_RATIO), 1)
    if total == alert_at:
        logger.warning("public_cv_cap_near", used=total, cap=cap)
        await report_incident(
            kind="alert",
            fingerprint="public_cv:cap_near",
            title="Le CV gratuit approche de son plafond du jour",
            context="CV gratuit sans compte",
            where=f"{total} adaptations sur {cap} en 24 h",
            technical_cause="PUBLIC_CV_DAILY_GLOBAL_CAP bientôt atteint.",
        )


async def refund(ip: str) -> None:
    """Gives back the use taken by `consume` (the call failed on our side)."""
    await ratelimit.backend.refund(_visitor_key(ip))
    await ratelimit.backend.refund(_GLOBAL_KEY)
