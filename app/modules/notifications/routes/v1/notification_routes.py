from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DBSession
from app.modules.auth import CurrentUser
from app.modules.notifications.schemas import (
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationTestSendResult,
)
from app.modules.notifications.service import (
    DailyBriefService,
    NotificationPreferenceService,
)

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


@router.post("/preferences/test-send", response_model=NotificationTestSendResult)
async def test_send_preferences(
    session: DBSession, user: CurrentUser
) -> NotificationTestSendResult:
    """ "Tester l'envoi" button on the notification-settings screen -- sends
    one real brief right now, on whatever channels are currently enabled and
    valid, instead of waiting for the scheduled 18:30 job. See
    DailyBriefService.send_test_brief for exactly how this differs from the
    real daily run (ignores dedup, records nothing)."""
    channels_sent = await DailyBriefService(session).send_test_brief(user.id)
    return NotificationTestSendResult(channels_sent=channels_sent)
