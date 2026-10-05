from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def as_utc(moment: datetime) -> datetime:
    """SQLite hands datetimes back naive (Postgres keeps the timezone)."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def now_utc() -> datetime:
    return datetime.now(UTC)


def start_of_today_paris() -> datetime:
    local = datetime.now(PARIS).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def paris_day(moment: datetime) -> date:
    return as_utc(moment).astimezone(PARIS).date()


def last_days(count: int) -> list[date]:
    """The last `count` Paris calendar days, oldest first, ending today."""
    today = datetime.now(PARIS).date()
    return [today - timedelta(days=offset) for offset in range(count - 1, -1, -1)]


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def normalize_path(raw: str) -> str | None:
    """ "/opportunites/3f2a…?x=1#top" -> "/opportunites/:id"; None for a path
    that must not be recorded (admin area, unsubscribe page, junk)."""
    path = raw.split("?", 1)[0].split("#", 1)[0].strip()
    if not path.startswith("/") or len(path) > 200:
        return None
    path = _UUID_RE.sub(":id", path)
    path = re.sub(r"/\d{4,}(?=/|$)", "/:id", path)
    if len(path) > 1:
        path = path.rstrip("/")
    if path.startswith(("/admin", "/annonces")):
        return None
    return path


def parse_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None
