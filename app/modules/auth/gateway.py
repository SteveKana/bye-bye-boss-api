"""Synchronous cross-module access point for auth data.

Other modules depend on `AuthGateway` (and the `PublicUser` DTO) instead of
importing auth's ORM model / repository directly. This keeps module boundaries
explicit: swap auth's internals freely as long as the gateway contract holds.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import PublicUser
from app.modules.auth.service import AuthService


class AuthGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def get_user(self, user_id: uuid.UUID) -> PublicUser | None:
        user = await self.users.get(user_id)
        return PublicUser.model_validate(user) if user else None

    async def user_exists(self, user_id: uuid.UUID) -> bool:
        return await self.users.get(user_id) is not None

    async def queue_verification_email(self, user_id: uuid.UUID) -> bool:
        """Queue a fresh verification e-mail for an account that has not
        confirmed its address (admin dashboard action). False when there is
        nothing to send: unknown, inactive or already verified."""
        user = await self.users.get(user_id)
        if user is None or not user.is_active or user.is_verified:
            return False
        await AuthService(self.session).queue_verification(user)
        return True
