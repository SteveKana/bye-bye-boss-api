"""Surface the mail setup at boot: a silently unconfigured provider in
production is the kind of thing you want to see in the logs on day one."""

from __future__ import annotations

from app.core.logging import get_logger
from app.modules.mailer.transports import active_provider, is_configured

logger = get_logger("mailer")


async def log_mail_setup() -> None:
    provider = active_provider()
    if is_configured():
        logger.info("mail_provider_ready", provider=provider)
    else:
        logger.warning(
            "mail_provider_unconfigured", provider=provider, delivery="log only"
        )
