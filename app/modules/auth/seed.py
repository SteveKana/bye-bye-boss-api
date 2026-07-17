"""Bootstrap admin seeding, run from the module's startup hook."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import get_logger
from app.modules.auth.schemas import UserCreate
from app.modules.auth.service import AuthService

logger = get_logger("auth.seed")


async def seed_admin() -> None:
    settings = get_settings()
    if not settings.ADMIN_EMAIL or not settings.ADMIN_PASSWORD:
        return
    async with AsyncSessionLocal() as session:
        service = AuthService(session)
        if await service.users.get_by_email(settings.ADMIN_EMAIL):
            return
        await service.register(
            UserCreate(
                email=settings.ADMIN_EMAIL,
                password=settings.ADMIN_PASSWORD,
                full_name="Administrator",
            ),
            isadmin=True,
        )
        logger.info("admin_seeded", email=settings.ADMIN_EMAIL)
