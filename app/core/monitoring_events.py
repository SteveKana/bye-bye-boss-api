"""Events through which any module tells the `monitoring` module about
something worth recording, without importing it (module boundaries, see
tests/test_architecture.py): technical incidents and AI token consumption.

Emitting is always best-effort -- `report_incident` / `record_ai_usage` never
raise, so instrumenting a code path can never break it.
"""

from __future__ import annotations

import uuid
from typing import Literal

from app.core.events import Event, event_bus
from app.core.logging import get_logger

logger = get_logger("app.monitoring")

IncidentKind = Literal["server", "alert", "cv", "browser", "other"]


class IncidentReported(Event):
    kind: IncidentKind
    # Identical incidents share a fingerprint and are grouped in one row.
    fingerprint: str
    title: str
    # Short "area" line shown under the title ("Alertes · WhatsApp").
    context: str = ""
    where: str = ""
    technical_cause: str = ""
    # What the end user saw, when there was something to see.
    user_message: str | None = None
    user_id: uuid.UUID | None = None


class AiUsageRecorded(Event):
    stage: str  # "prefilter" | "analysis"
    model: str
    requests: int
    input_tokens: int
    output_tokens: int


async def report_incident(
    *,
    kind: IncidentKind,
    fingerprint: str,
    title: str,
    context: str = "",
    where: str = "",
    technical_cause: str = "",
    user_message: str | None = None,
    user_id: uuid.UUID | None = None,
) -> None:
    try:
        await event_bus.emit(
            IncidentReported(
                kind=kind,
                fingerprint=fingerprint[:200],
                title=title[:200],
                context=context[:200],
                where=where[:300],
                technical_cause=technical_cause[:1000],
                user_message=user_message[:300] if user_message else None,
                user_id=user_id,
            )
        )
    except Exception:  # never let monitoring break the monitored code
        logger.exception("report_incident_failed")


async def record_ai_usage(
    *, stage: str, model: str, requests: int, input_tokens: int, output_tokens: int
) -> None:
    try:
        await event_bus.emit(
            AiUsageRecorded(
                stage=stage,
                model=model,
                requests=requests,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
        )
    except Exception:
        logger.exception("record_ai_usage_failed")
