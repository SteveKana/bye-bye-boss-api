"""Public (token-protected) opt-out of the market-watch digest."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends

from app.core.dependencies import DBSession
from app.core.exceptions import NotFoundError
from app.core.ratelimit import RateLimiter
from app.modules.auth import AuthGateway
from app.modules.market.models import MarketDigestOptOut
from app.modules.market.schemas import UnsubscribeBody, UnsubscribeInfo
from app.modules.market.service import is_opted_out
from app.modules.market.tokens import read_unsubscribe_token

router = APIRouter(prefix="/market/unsubscribe", tags=["market"])

limit = RateLimiter(times=30, seconds=60, scope="market:unsubscribe")


def mask_email(email: str) -> str:
    """ "julie.martin@example.com" -> "j***@example.com"."""
    name, _, domain = email.partition("@")
    return f"{name[:1]}***@{domain}" if domain else "***"


async def _email_of(session: DBSession, user_id: uuid.UUID) -> str:
    user = await AuthGateway(session).get_user(user_id)
    if user is None:
        raise NotFoundError("Compte introuvable.")
    return str(user.email)


@router.get("/info", response_model=UnsubscribeInfo, dependencies=[Depends(limit)])
async def info(token: str, session: DBSession) -> UnsubscribeInfo:
    user_id = read_unsubscribe_token(token)
    return UnsubscribeInfo(
        email=mask_email(await _email_of(session, user_id)),
        already=await is_opted_out(session, user_id),
    )


@router.post("", response_model=dict[str, str], dependencies=[Depends(limit)])
async def unsubscribe(data: UnsubscribeBody, session: DBSession) -> dict[str, str]:
    user_id = read_unsubscribe_token(data.token)
    await _email_of(session, user_id)
    if not await is_opted_out(session, user_id):
        session.add(MarketDigestOptOut(user_id=user_id))
        await session.commit()
    return {"detail": "Tu ne recevras plus la veille marché."}
