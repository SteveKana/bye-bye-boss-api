from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import event_bus
from app.core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_reset_token,
    create_verify_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.auth.emails import build_reset_email, build_verification_email
from app.modules.auth.events import UserRegistered
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import TokenPair, UserCreate, UserUpdate
from app.modules.mailer import MailerGateway

logger = get_logger("auth")


class AuthService:
    """Owns the auth unit-of-work. Commits its own transaction so emitted
    events observe persisted state (get_session provides a safety-net commit)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(
        self, data: UserCreate, *, isadmin: bool = False, auto_verify: bool = False
    ) -> User:
        if await self.users.get_by_email(data.email):
            raise ConflictError("A user with this email already exists.")
        user = await self.users.create(
            User(
                email=data.email,
                password_hash=hash_password(data.password),
                first_name=data.first_name,
                last_name=data.last_name,
                is_verified=auto_verify,
                isadmin=isadmin,
            )
        )
        # Queue the verification email in the same transaction as the new user
        # (transactional outbox): a verification mail always refers to a real
        # account, and none is lost.
        if not auto_verify:
            await self._queue_verification_email(user, data.locale)

        await self.session.commit()
        logger.info("user_registered", user_id=str(user.id), email=user.email)
        await event_bus.emit(
            UserRegistered(
                user_id=user.id,
                email=user.email,
                first_name=user.first_name,
                last_name=user.last_name,
            )
        )
        return user

    async def _queue_verification_email(self, user: User, locale: str) -> None:
        token = create_verify_token(str(user.id))
        mail = build_verification_email(locale, token)
        await MailerGateway(self.session).enqueue(
            to_email=user.email, subject=mail.subject, text=mail.text, html=mail.html
        )

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.users.get_by_email(email)
        if not user or not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid credentials.")
        if not user.is_active:
            raise UnauthorizedError("Account is disabled.")
        if not user.is_verified:
            raise ForbiddenError(
                "Please verify your email address first.", code="email_not_verified"
            )
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

    async def request_password_reset(self, email: str, locale: str) -> str | None:
        """Queue a reset email and return the token (surfaced only in debug).

        No-op and no enumeration when the address is unknown or inactive.
        """
        user = await self.users.get_by_email(email)
        if not user or not user.is_active:
            return None
        token = create_reset_token(str(user.id))
        mail = build_reset_email(locale, token)
        await MailerGateway(self.session).enqueue(
            to_email=user.email, subject=mail.subject, text=mail.text, html=mail.html
        )
        await self.session.commit()
        logger.info("password_reset_requested", user_id=str(user.id), email=user.email)
        return token

    async def confirm_password_reset(self, token: str, new_password: str) -> None:
        payload = decode_token(token, expected_type="reset")
        user = await self.users.get(uuid.UUID(payload["sub"]))
        if not user or not user.is_active:
            raise UnauthorizedError("Invalid reset token.")
        user.password_hash = hash_password(new_password)
        self.session.add(user)
        await self.session.commit()
        logger.info("password_reset_confirmed", user_id=str(user.id))

    async def update_profile(self, user: User, data: UserUpdate) -> User:
        if data.first_name is not None:
            user.first_name = data.first_name
        if data.last_name is not None:
            user.last_name = data.last_name
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        logger.info("profile_updated", user_id=str(user.id))
        return user

    async def change_password(self, user: User, current: str, new: str) -> None:
        if not verify_password(current, user.password_hash):
            raise UnauthorizedError("Current password is incorrect.")
        user.password_hash = hash_password(new)
        self.session.add(user)
        await self.session.commit()
        logger.info("password_changed", user_id=str(user.id))

    async def verify_email(self, token: str) -> None:
        payload = decode_token(token, expected_type="verify")
        user = await self.users.get(uuid.UUID(payload["sub"]))
        if not user:
            raise UnauthorizedError("Invalid verification token.")
        if not user.is_verified:
            user.is_verified = True
            self.session.add(user)
            await self.session.commit()
            logger.info("email_verified", user_id=str(user.id))

    async def resend_verification(self, email: str, locale: str) -> None:
        """Re-send the verification email. No-op (and no enumeration) when the
        address is unknown, inactive, or already verified."""
        user = await self.users.get_by_email(email)
        if not user or not user.is_active or user.is_verified:
            return
        await self._queue_verification_email(user, locale)
        await self.session.commit()
        logger.info("verification_resent", user_id=str(user.id), email=email)
