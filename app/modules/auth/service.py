from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import event_bus
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.auth.events import UserRegistered
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import TokenPair, UserCreate

logger = get_logger("auth")


class AuthService:
    """Owns the auth unit-of-work. Commits its own transaction so emitted
    events observe persisted state (get_session provides a safety-net commit)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, data: UserCreate, *, isadmin: bool = False) -> User:
        if await self.users.get_by_email(data.email):
            raise ConflictError("A user with this email already exists.")
        user = await self.users.create(
            User(
                email=data.email,
                password_hash=hash_password(data.password),
                full_name=data.full_name,
                isadmin=isadmin,
            )
        )
        await self.session.commit()
        logger.info("user_registered", user_id=str(user.id), email=user.email)
        await event_bus.emit(
            UserRegistered(user_id=user.id, email=user.email, full_name=user.full_name)
        )
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.users.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid credentials.")
        if not user.is_active:
            raise UnauthorizedError("Account is disabled.")
        return user

    def issue_tokens(self, user: User) -> TokenPair:
        claims = {
            "email": user.email,
            "isadmin": user.isadmin,
        }
        return TokenPair(
            access_token=create_access_token(str(user.id), claims),
            refresh_token=create_refresh_token(str(user.id)),
        )

    async def login(self, email: str, password: str) -> TokenPair:
        user = await self.authenticate(email, password)
        return self.issue_tokens(user)

    async def refresh(self, refresh_token: str) -> TokenPair:
        payload = decode_token(refresh_token, expected_type="refresh")
        user = await self.users.get(uuid.UUID(payload["sub"]))
        if not user or not user.is_active:
            raise UnauthorizedError("Invalid refresh token.")
        return self.issue_tokens(user)

    async def request_password_reset(self, email: str) -> str | None:
        """Return a short-lived reset token, or None if no eligible account.

        The caller must not leak which case happened (no user enumeration).
        Delivery (email) is out of scope; the token is surfaced by the route
        only in debug and always logged.
        """
        user = await self.users.get_by_email(email)
        if not user or not user.is_active:
            return None
        logger.info("password_reset_requested", user_id=str(user.id), email=user.email)
        return create_reset_token(str(user.id))

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        payload = decode_token(token, expected_type="reset")
        user = await self.users.get(uuid.UUID(payload["sub"]))
        if not user or not user.is_active:
            raise UnauthorizedError("Invalid reset token.")
        user.password_hash = hash_password(new_password)
        self.session.add(user)
        await self.session.commit()
        logger.info("password_reset_confirmed", user_id=str(user.id))
