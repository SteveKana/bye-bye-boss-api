from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.dependencies import DBSession
from app.core.ratelimit import RateLimiter
from app.core.security import decode_token
from app.modules.auth import CurrentUser
from app.modules.monitoring.common import normalize_path
from app.modules.monitoring.incidents import IncidentService, browser_fingerprint
from app.modules.monitoring.models import PageView
from app.modules.monitoring.schemas import ClientErrorTrack, PageTrack

router = APIRouter(prefix="/monitoring/track", tags=["monitoring"])

page_limit = RateLimiter(times=240, seconds=60, scope="monitoring:page")
error_limit = RateLimiter(times=30, seconds=60, scope="monitoring:client_error")


def _browser_family(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    for needle, name in (
        ("edg/", "Edge"),
        ("firefox", "Firefox"),
        ("chrome", "Chrome"),
        ("safari", "Safari"),
    ):
        if needle in ua:
            device = "mobile" if "mobile" in ua or "iphone" in ua else "ordinateur"
            return f"{name} {device}"
    return "navigateur inconnu"


_bearer = HTTPBearer(auto_error=False)


async def _optional_user_id(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID | None:
    if creds is None:
        return None
    try:
        return uuid.UUID(decode_token(creds.credentials)["sub"])
    except Exception:
        return None


OptionalUserId = Annotated[uuid.UUID | None, Depends(_optional_user_id)]


@router.post(
    "/page",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(page_limit)],
)
async def track_page(
    data: PageTrack, session: DBSession, user: CurrentUser
) -> Response:
    path = normalize_path(data.path)
    if path is not None:
        session.add(PageView(user_id=user.id, path=path))
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/client-error",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(error_limit)],
)
async def track_client_error(
    data: ClientErrorTrack, session: DBSession, user_id: OptionalUserId
) -> Response:
    message = data.message.strip()
    path = normalize_path(data.path or "") or (data.path or "")[:100]
    await IncidentService(session).record(
        kind="browser",
        fingerprint=browser_fingerprint(message),
        title=f"Erreur d'affichage : {message[:90]}",
        context=f"Navigateur · {_browser_family(data.user_agent)}",
        where=path or "page inconnue",
        technical_cause=(data.stack or message)[:1000],
        user_message=None,
        user_id=user_id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
