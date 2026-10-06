from __future__ import annotations

import uuid

from app.core.events import Event


class MatchesScored(Event):
    """Fired by the batch pipeline (batch_service.py) once new matches have
    been fully analysed for these candidate profiles -- the cue for the
    notifications module to send their daily brief (it replaces the former
    fixed 18:30 schedule, since with the Batch API there is no fixed moment
    at which results are ready)."""

    profile_ids: list[uuid.UUID]
