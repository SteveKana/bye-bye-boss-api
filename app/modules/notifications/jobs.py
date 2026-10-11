"""One brief per candidate per day (Steve, 2026-10-10).

Matching results come back from OpenAI's Batch API in several waves at no
fixed time, and matching's `MatchesScored` event fires for each wave (see
listeners.py). The brief therefore waits until a candidate has nothing left
to analyse (service.py, `_should_wait`) and never leaves twice in a day.

This job is the safety net: at the cut-off hour (Paris time) every candidate
who has not received today's brief gets it with whatever is ready, so a slow
batch can never hold a brief back for the whole day.
"""

from __future__ import annotations

from app.core.database import AsyncSessionLocal
from app.core.scheduler import scheduled
from app.modules.notifications.service import (
    BRIEF_CUTOFF_HOUR_PARIS,
    DailyBriefService,
)


@scheduled(
    cron=f"0 {BRIEF_CUTOFF_HOUR_PARIS} * * *",
    timezone="Europe/Paris",
    id="notifications_cutoff_brief",
)
async def send_cutoff_briefs() -> None:
    async with AsyncSessionLocal() as session:
        await DailyBriefService(session).send_due_briefs()
