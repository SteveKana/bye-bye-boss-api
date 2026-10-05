from __future__ import annotations

from sqlalchemy import delete
from sqlmodel import col, select

from app.core.database import AsyncSessionLocal
from app.core.events import on
from app.core.logging import get_logger
from app.core.monitoring_events import AiUsageRecorded, IncidentReported
from app.modules.auth import UserDeletionRequested
from app.modules.monitoring.incidents import IncidentService
from app.modules.monitoring.models import (
    AiUsage,
    AnnouncementOptOut,
    Incident,
    PageView,
)

logger = get_logger("monitoring.listeners")


@on(IncidentReported)
async def store_incident(event: IncidentReported) -> None:
    async with AsyncSessionLocal() as session:
        await IncidentService(session).record(
            kind=event.kind,
            fingerprint=event.fingerprint,
            title=event.title,
            context=event.context,
            where=event.where,
            technical_cause=event.technical_cause,
            user_message=event.user_message,
            user_id=event.user_id,
        )
        await session.commit()


@on(AiUsageRecorded)
async def store_ai_usage(event: AiUsageRecorded) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            AiUsage(
                stage=event.stage,
                model=event.model,
                requests=event.requests,
                input_tokens=event.input_tokens,
                output_tokens=event.output_tokens,
            )
        )
        await session.commit()


@on(UserDeletionRequested)
async def purge_monitoring_data(event: UserDeletionRequested) -> None:
    """Account deletion: the user's page views, their unsubscribe record, and
    their id inside incident groups (the incident itself stays)."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(PageView).where(col(PageView.user_id) == event.user_id)
        )
        await session.execute(
            delete(AnnouncementOptOut).where(
                col(AnnouncementOptOut.user_id) == event.user_id
            )
        )
        uid = str(event.user_id)
        for incident in (await session.exec(select(Incident))).all():
            if uid in (incident.user_ids or []):
                incident.user_ids = [u for u in incident.user_ids if u != uid]
                session.add(incident)
        await session.commit()
