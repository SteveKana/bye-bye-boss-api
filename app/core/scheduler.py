"""Optional background scheduler (APScheduler).

Register jobs from anywhere at import time:

    from app.core.scheduler import scheduled

    @scheduled(interval_minutes=5)
    async def sync_data() -> None:
        ...

    @scheduled(cron="0 0 1 * *")   # 1st of month, 00:00 UTC
    async def monthly_report() -> None:
        ...

    @scheduled(cron="0 18 * * *", timezone="Europe/Paris")   # 18:00 Paris time
    async def evening_job() -> None:
        ...

Jobs only run if `SCHEDULER_ENABLED` is true. The scheduler is started/stopped
by the app lifespan.

`cron` is interpreted in the scheduler's own timezone (UTC, see `scheduler`
below) unless `timezone` is given -- pass it whenever the schedule is meant
to track a human's clock (e.g. "18:00 in France") rather than a fixed UTC
instant, so it keeps landing at the same local time across DST changes
instead of drifting by an hour twice a year.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.scheduler")

Job = Callable[[], Awaitable[None]]


@dataclass
class _JobSpec:
    func: Job
    trigger: object
    id: str


_registry: list[_JobSpec] = []
scheduler = AsyncIOScheduler(timezone="UTC")


def scheduled(
    *,
    interval_minutes: int | None = None,
    cron: str | None = None,
    timezone: str | None = None,
    id: str | None = None,
) -> Callable[[Job], Job]:
    if (interval_minutes is None) == (cron is None):
        raise ValueError("Provide exactly one of `interval_minutes` or `cron`.")
    if timezone is not None and interval_minutes is not None:
        raise ValueError("`timezone` only applies to a `cron` trigger.")

    def decorator(func: Job) -> Job:
        trigger: object
        if interval_minutes is not None:
            trigger = IntervalTrigger(minutes=interval_minutes)
        else:
            trigger = CronTrigger.from_crontab(cron, timezone=timezone)  # type: ignore[arg-type]
        _registry.append(_JobSpec(func=func, trigger=trigger, id=id or func.__name__))
        return func

    return decorator


def start_scheduler() -> None:
    settings = get_settings()
    if not settings.SCHEDULER_ENABLED or not _registry:
        return
    for spec in _registry:
        scheduler.add_job(spec.func, spec.trigger, id=spec.id, replace_existing=True)
    scheduler.start()
    logger.info("scheduler_started", jobs=[s.id for s in _registry])


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("scheduler_stopped")
