from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.exceptions import NotFoundError
from app.modules.monitoring import queries
from app.modules.monitoring.common import (
    as_utc,
    last_days,
    mask_email,
    now_utc,
    paris_day,
    parse_uuid,
)
from app.modules.monitoring.models import Incident, IncidentStatus
from app.modules.monitoring.schemas import (
    DayCount,
    IncidentDetail,
    IncidentListItem,
    IncidentsResponse,
    IncidentTiles,
)

_MAX_OCCURRENCES = 20
_MAX_USERS = 50
OPEN = (IncidentStatus.new.value, IncidentStatus.in_progress.value)


def browser_fingerprint(message: str) -> str:
    """Same error text -> same incident, whatever the numbers/ids inside."""
    normalized = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}|\d+", "#", message.lower())[:200]
    return "browser:" + hashlib.sha1(normalized.encode()).hexdigest()[:16]


def to_item(incident: Incident) -> IncidentListItem:
    return IncidentListItem(
        id=incident.id,
        status=incident.status,  # type: ignore[arg-type]
        kind=incident.kind,
        title=incident.title,
        context=incident.context,
        count=incident.count,
        affected_users=len(incident.user_ids or []),
        first_seen=as_utc(incident.first_seen),
        last_seen=as_utc(incident.last_seen),
        resolved_at=as_utc(incident.resolved_at) if incident.resolved_at else None,
    )


class IncidentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        *,
        kind: str,
        fingerprint: str,
        title: str,
        context: str = "",
        where: str = "",
        technical_cause: str = "",
        user_message: str | None = None,
        user_id: uuid.UUID | None = None,
    ) -> Incident:
        now = now_utc()
        existing = (
            await self.session.exec(
                select(Incident).where(Incident.fingerprint == fingerprint)
            )
        ).first()
        if existing is None:
            incident = Incident(
                fingerprint=fingerprint,
                kind=kind,
                title=title,
                context=context,
                origin=where,
                technical_cause=technical_cause,
                user_message=user_message,
                count=1,
                user_ids=[str(user_id)] if user_id else [],
                occurrences=[now.isoformat()],
                first_seen=now,
                last_seen=now,
            )
            self.session.add(incident)
            await self.session.flush()
            return incident
        # New list objects (not in-place edits) so the JSON columns see the change.
        user_ids = list(existing.user_ids or [])
        if user_id and str(user_id) not in user_ids and len(user_ids) < _MAX_USERS:
            user_ids.append(str(user_id))
        existing.user_ids = user_ids
        existing.occurrences = [now.isoformat(), *(existing.occurrences or [])][
            :_MAX_OCCURRENCES
        ]
        existing.count += 1
        existing.last_seen = now
        existing.technical_cause = technical_cause or existing.technical_cause
        existing.user_message = user_message or existing.user_message
        if existing.status == IncidentStatus.resolved.value:
            # It happened again: back to the top of the list.
            existing.status = IncidentStatus.new.value
            existing.resolved_at = None
        self.session.add(existing)
        await self.session.flush()
        return existing

    async def _get(self, incident_id: uuid.UUID) -> Incident:
        incident = await self.session.get(Incident, incident_id)
        if incident is None:
            raise NotFoundError("Incident introuvable.")
        return incident

    async def list(self, *, status: str, days: int = 7) -> IncidentsResponse:
        all_rows = list((await self.session.exec(select(Incident))).all())
        if status == "resolved":
            rows = [r for r in all_rows if r.status == IncidentStatus.resolved.value]
        elif status == "all":
            rows = all_rows
        else:
            rows = [r for r in all_rows if r.status in OPEN]
        rows.sort(key=lambda r: as_utc(r.last_seen), reverse=True)

        since = now_utc() - timedelta(days=days)
        open_rows = [r for r in all_rows if r.status in OPEN]
        affected = {uid for r in open_rows for uid in (r.user_ids or [])}
        tiles = IncidentTiles(
            open=len(open_rows),
            new=sum(1 for r in open_rows if r.status == IncidentStatus.new.value),
            affected_users=len(affected),
            server_errors=_occurrences_since(all_rows, "server", since),
            browser_errors=_occurrences_since(all_rows, "browser", since),
            resolved_period=sum(
                1 for r in all_rows if r.resolved_at and as_utc(r.resolved_at) >= since
            ),
        )
        per_day = dict.fromkeys(last_days(7), 0)
        for row in all_rows:
            for stamp in row.occurrences or []:
                day = paris_day(datetime.fromisoformat(stamp))
                if day in per_day:
                    per_day[day] += 1
        return IncidentsResponse(
            items=[to_item(r) for r in rows],
            tiles=tiles,
            per_day=[DayCount(date=d.isoformat(), count=n) for d, n in per_day.items()],
        )

    async def detail(self, incident_id: uuid.UUID) -> IncidentDetail:
        incident = await self._get(incident_id)
        ids = [i for i in (parse_uuid(u) for u in incident.user_ids or []) if i]
        emails: list[str] = []
        if ids:
            result = await self.session.execute(
                sa.select(queries.users.c.email).where(queries.users.c.id.in_(ids))
            )
            emails = [mask_email(row[0]) for row in result.all()]
        item = to_item(incident)
        return IncidentDetail(
            **item.model_dump(),
            occurrences=[datetime.fromisoformat(s) for s in incident.occurrences or []],
            users=emails,
            where=incident.origin,
            technical_cause=incident.technical_cause,
            user_message=incident.user_message,
        )

    async def set_status(self, incident_id: uuid.UUID, status: str) -> IncidentListItem:
        incident = await self._get(incident_id)
        incident.status = status
        incident.resolved_at = (
            now_utc() if status == IncidentStatus.resolved.value else None
        )
        self.session.add(incident)
        await self.session.commit()
        return to_item(incident)


def _occurrences_since(rows: list[Incident], kind: str, since: datetime) -> int:
    total = 0
    for row in rows:
        if row.kind != kind:
            continue
        total += sum(
            1
            for stamp in row.occurrences or []
            if as_utc(datetime.fromisoformat(stamp)) >= since
        )
    return total
