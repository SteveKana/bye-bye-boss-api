"""Queue worker: flushes pending mail on a fixed interval."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.scheduler import scheduled
from app.modules.mailer.service import MailerService

logger = get_logger("mailer.worker")


@scheduled(interval_minutes=get_settings().MAIL_QUEUE_INTERVAL_MINUTES, id="mail_queue")
async def flush_mail_queue() -> None:
    async with AsyncSessionLocal() as session:
        sent = await MailerService(session).process_pending()
    if sent:
        logger.info("mail_queue_flushed", sent=sent)
