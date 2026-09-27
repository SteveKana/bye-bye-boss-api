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
    create_verify_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.modules.auth.emails import build_reset_email, build_verification_email
from app.modules.auth.events import UserRegistered
from app.modules.auth.google_oauth import verify_google_id_token
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
    ) -> TokenPair:
        """Creates the account and signs it straight in.

        Steve's call: registering behaves exactly like Google sign-in --
        a full session immediately, no wait for email verification. The
        verification email is still queued and is_verified still gets set
        by the normal verify-email flow, but nothing here or in
        `authenticate` blocks on it anymore; it's informational only. This
        is what lets the frontend send a fresh signup straight into
        onboarding (CV upload) without a "check your email" detour.
        """
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
        return self.issue_tokens(user)

    async def _queue_verification_email(self, user: User, locale: str) -> None:
        token = create_verify_token(str(user.id))
        mail = build_verification_email(locale, token)
        await MailerGateway(self.session).enqueue(
            to_email=user.email, subject=mail.subject, text=mail.text, html=mail.html
        )

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.users.get_by_email(email)
        # password_hash is None for an account created via Google sign-in
        # that never went through "mot de passe oublié" to set one -- there
        # is nothing to check a password against, so this must fail the
        # same way a wrong password would (not a 500 from bcrypt choking on
        # None), without hinting to the caller which case it hit. Split into
        # two checks (rather than one `or`-chain) so the password_hash is
        # str, not str | None, by the time verify_password sees it.
        if not user or not user.password_hash:
            raise UnauthorizedError("Invalid credentials.")
        if not verify_password(password, user.password_hash):
            raise UnauthorizedError("Invalid credentials.")
        if not user.is_active:
            raise UnauthorizedError("Account is disabled.")
        # is_verified is no longer a login gate (Steve's call): registering
        # signs the user in immediately, same as Google, so a not-yet-verified
        # account must still be able to log back in afterwards.
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

    async def login_with_google(
        self, id_token: str, *, client_id: str
    ) -> tuple[TokenPair, bool]:
        """Verifies the Google ID token, then either logs into or creates the
        matching account, keyed by email. Returns the tokens plus whether
        this created a brand-new account, so the frontend can send a first-
        time Google signup into onboarding (CV upload) instead of the
        dashboard -- the same destination a fresh email/password signup gets.

        Matching by email rather than only by Google's own subject id is a
        deliberate choice (Steve's call): Google already vouches for the
        email's ownership, so a visitor who originally registered with a
        password and later taps "Continuer avec Google" with that same
        address gets signed into that same account rather than blocked or
        silently given a second one -- there is exactly one account per
        email in this system, full stop.
        """
        claims = verify_google_id_token(id_token, client_id=client_id)
        email = claims["email"]
        google_id = claims["sub"]

        user = await self.users.get_by_email(email)
        # Checked before touching anything: a disabled account shouldn't
        # gain a Google link (or any other side effect) just from a rejected
        # sign-in attempt.
        if user is not None and not user.is_active:
            raise UnauthorizedError("Account is disabled.")

        is_new_user = user is None
        if user is None:
            user = await self.users.create(
                User(
                    email=email,
                    password_hash=None,
                    first_name=claims.get("given_name"),
                    last_name=claims.get("family_name"),
                    # Google already verified this address -- our own
                    # verification email would be redundant.
                    is_verified=True,
                    google_id=google_id,
                )
            )
            await self.session.commit()
            logger.info(
                "user_registered_via_google", user_id=str(user.id), email=user.email
            )
            await event_bus.emit(
                UserRegistered(
                    user_id=user.id,
                    email=user.email,
                    first_name=user.first_name,
                    last_name=user.last_name,
                )
            )
        elif user.google_id != google_id:
            # Existing account (password-based, or linked to a different
            # Google identity) signing in with Google for the first time --
            # record the link for next time, don't touch anything else.
            user.google_id = google_id
            self.session.add(user)
            await self.session.commit()
            logger.info("google_account_linked", user_id=str(user.id))

        return self.issue_tokens(user), is_new_user

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
        # A Google-only account (password_hash is None) has no current
        # password to check against -- rather than crashing on None, this
        # rejects the same way a wrong password would. Such a user still has
        # a path to a first password: the "mot de passe oublié" / reset flow
        # (confirm_password_reset below) sets one unconditionally.
        if not user.password_hash or not verify_password(current, user.password_hash):
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
