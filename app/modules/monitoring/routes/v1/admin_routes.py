from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query

from app.core.dependencies import DBSession
from app.modules.auth import AdminUser
from app.modules.monitoring import accounts, announcements, stats
from app.modules.monitoring.incidents import IncidentService
from app.modules.monitoring.schemas import (
    AccountAction,
    AnnouncementContent,
    AnnouncementHistoryItem,
    AnnouncementPreview,
    AnnouncementSend,
    AnnouncementSendResult,
    Audience,
    BehaviorResponse,
    Detail,
    IncidentDetail,
    IncidentListItem,
    IncidentPatch,
    IncidentsResponse,
    OverviewResponse,
    UserRow,
)

router = APIRouter(prefix="/monitoring", tags=["monitoring"])

Days = Query(7, description="Period in days (1, 7 or 30).")


def _days(value: int) -> int:
    return value if value in (1, 7, 30) else 7


@router.get("/overview", response_model=OverviewResponse)
async def overview(
    session: DBSession, _: AdminUser, days: int = Days
) -> OverviewResponse:
    return await stats.overview(session, _days(days))


@router.get("/behavior", response_model=BehaviorResponse)
async def behavior(
    session: DBSession, _: AdminUser, days: int = Days
) -> BehaviorResponse:
    return await stats.behavior(session, _days(days))


@router.get("/users", response_model=list[UserRow])
async def users(session: DBSession, _: AdminUser) -> list[UserRow]:
    return await stats.users_list(session)


@router.post("/accounts/{user_id}/action", response_model=Detail)
async def account_action(
    user_id: uuid.UUID, data: AccountAction, session: DBSession, _: AdminUser
) -> Detail:
    return Detail(detail=await accounts.run_action(session, user_id, data.action))


# ------------------------------------------------------------- incidents --
@router.get("/incidents", response_model=IncidentsResponse)
async def incidents(
    session: DBSession,
    _: AdminUser,
    status: Literal["open", "resolved", "all"] = "open",
) -> IncidentsResponse:
    return await IncidentService(session).list(status=status)


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def incident_detail(
    incident_id: uuid.UUID, session: DBSession, _: AdminUser
) -> IncidentDetail:
    return await IncidentService(session).detail(incident_id)


@router.patch("/incidents/{incident_id}", response_model=IncidentListItem)
async def incident_patch(
    incident_id: uuid.UUID, data: IncidentPatch, session: DBSession, _: AdminUser
) -> IncidentListItem:
    return await IncidentService(session).set_status(incident_id, data.status)


# ---------------------------------------------------------- announcements --
@router.get("/announcements/audiences", response_model=list[Audience])
async def audiences(session: DBSession, _: AdminUser) -> list[Audience]:
    return [
        Audience(key=key, label=label, count=count)
        for key, label, count in await announcements.audience_counts(session)
    ]


@router.post("/announcements/preview", response_model=AnnouncementPreview)
async def preview(data: AnnouncementContent, admin: AdminUser) -> AnnouncementPreview:
    mail = announcements.render_announcement(
        data.subject, data.body, first_name="Julie", user_id=admin.id
    )
    return AnnouncementPreview(
        subject=mail.subject, text=mail.text, html=mail.html or ""
    )


@router.post("/announcements/test", response_model=Detail)
async def test_send(
    data: AnnouncementContent, session: DBSession, admin: AdminUser
) -> Detail:
    await announcements.send_test(
        session,
        admin_id=admin.id,
        admin_email=admin.email,
        subject=data.subject,
        body=data.body,
    )
    return Detail(detail=f"Test envoyé à {admin.email}.")


@router.post("/announcements", response_model=AnnouncementSendResult)
async def send(
    data: AnnouncementSend, session: DBSession, admin: AdminUser
) -> AnnouncementSendResult:
    announcement = await announcements.send_announcement(
        session,
        admin_id=admin.id,
        subject=data.subject,
        body=data.body,
        audience=data.audience,
        expected_count=data.expected_count,
    )
    return AnnouncementSendResult(
        id=announcement.id,
        sent=announcement.sent,
        skipped_unsubscribed=announcement.skipped_unsubscribed,
    )


@router.get("/announcements", response_model=list[AnnouncementHistoryItem])
async def announcement_history(
    session: DBSession, _: AdminUser
) -> list[AnnouncementHistoryItem]:
    items = []
    for row in await announcements.history(session):
        items.append(
            AnnouncementHistoryItem(
                id=row.id,
                created_at=announcements.history_created(row),
                subject=row.subject,
                audience=row.audience,
                audience_label=announcements.AUDIENCE_LABELS.get(
                    row.audience, row.audience
                ),
                sent=row.sent,
                failed=await announcements.failed_count(session, row),
            )
        )
    return items
