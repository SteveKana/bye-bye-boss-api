from __future__ import annotations

import uuid

import sqlalchemy as sa
from fastapi import APIRouter, Depends

from app.core.dependencies import DBSession
from app.core.exceptions import NotFoundError
from app.core.ratelimit import RateLimiter
from app.modules.monitoring import queries as q
from app.modules.monitoring.common import mask_email
from app.modules.monitoring.models import AnnouncementOptOut
from app.modules.monitoring.schemas import Detail, UnsubscribeBody, UnsubscribeInfo
from app.modules.monitoring.tokens import read_unsubscribe_token

router = APIRouter(prefix="/monitoring/unsubscribe", tags=["monitoring"])

limit = RateLimiter(times=30, seconds=60, scope="monitoring:unsubscribe")


async def _email_of(session: DBSession, user_id) -> str:
    row = (
        await session.execute(sa.select(q.users.c.email).where(q.users.c.id == user_id))
    ).first()
    if row is None:
        raise NotFoundError("Compte introuvable.")
    return row[0]


async def _already(session: DBSession, user_id: uuid.UUID) -> bool:
    row: object = (
        await session.execute(
            sa.select(sa.column("id"))
            .select_from(AnnouncementOptOut)
            .where(sa.column("user_id") == user_id)
        )
    ).first()
    return row is not None


@router.get("/info", response_model=UnsubscribeInfo, dependencies=[Depends(limit)])
async def info(token: str, session: DBSession) -> UnsubscribeInfo:
    user_id = read_unsubscribe_token(token)
    return UnsubscribeInfo(
        email=mask_email(await _email_of(session, user_id)),
        already=await _already(session, user_id),
    )


@router.post("", response_model=Detail, dependencies=[Depends(limit)])
async def unsubscribe(data: UnsubscribeBody, session: DBSession) -> Detail:
    user_id = read_unsubscribe_token(data.token)
    await _email_of(session, user_id)
    if not await _already(session, user_id):
        session.add(AnnouncementOptOut(user_id=user_id))
        await session.commit()
    return Detail(detail="Vous ne recevrez plus d'annonces.")
