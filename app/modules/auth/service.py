from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import event_bus
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
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

    async def register(self, data: UserCreate, *, is_admin: bool = False) -> User:
        if await self.users.get_by_email(data.email):
            raise ConflictError("A user with this email already exists.")
        user = await self.users.create(
            User(
                email=data.email,
                password_hash=hash_password(data.password),
                full_name=data.full_name,
                is_admin=is_admin,
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
            "is_admin": user.is_admin,
            "role": "admin" if user.is_admin else "user",
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
