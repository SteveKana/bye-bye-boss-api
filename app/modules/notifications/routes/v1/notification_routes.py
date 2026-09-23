from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DBSession
from app.modules.auth import CurrentUser
from app.modules.notifications.schemas import (
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
)
from app.modules.notifications.service import NotificationPreferenceService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/preferences", response_model=NotificationPreferenceRead)
async def get_preferences(
    session: DBSession, user: CurrentUser
) -> NotificationPreferenceRead:
    """Backs the notification-settings screen -- creates a default row
    (email on, Discord/WhatsApp off) on first read rather than requiring a
    separate "initialize my preferences" call."""
    preference = await NotificationPreferenceService(session).get_or_create(user.id)
    return NotificationPreferenceRead.model_validate(preference)


@router.put("/preferences", response_model=NotificationPreferenceRead)
async def update_preferences(
    payload: NotificationPreferenceUpdate, session: DBSession, user: CurrentUser
) -> NotificationPreferenceRead:
    preference = await NotificationPreferenceService(session).update(
        user.id, **payload.model_dump(exclude_unset=True)
    )
    return NotificationPreferenceRead.model_validate(preference)
