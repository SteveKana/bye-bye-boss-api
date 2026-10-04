from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.core.schemas import BaseSchema


class UserCreate(BaseSchema):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str | None = None
    last_name: str | None = None
    # Locale for the verification email.
    locale: Literal["fr", "en"] = "fr"


class UserRead(BaseSchema):
    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
    is_active: bool
    is_verified: bool
    isadmin: bool
    subscription: str
    last_rescoring_time: datetime | None
    created_at: datetime
    # From Google's ID token "picture" claim -- None for an account that
    # never signed in with Google (see AuthService.login_with_google and
    # models.py's docstring on the column).
    picture_url: str | None


class UserUpdate(BaseSchema):
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)


class ChangePasswordRequest(BaseSchema):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class DeleteAccountRequest(BaseSchema):
    """The caller re-types their own email as the explicit confirmation
    (Google-created accounts have no password to ask for)."""

    email: str


class LoginRequest(BaseSchema):
    email: EmailStr
    password: str


class RefreshRequest(BaseSchema):
    refresh_token: str


class GoogleAuthRequest(BaseSchema):
    # The ID token (a signed JWT) handed to the frontend by Google Identity
    # Services -- verified server-side in AuthService.login_with_google
    # (see modules/auth/google_oauth.py). Not an OAuth "authorization code":
    # there's no exchange step, this token already carries the identity.
    id_token: str


class PasswordResetRequest(BaseSchema):
    email: EmailStr
    locale: Literal["fr", "en"] = "fr"


class PasswordResetConfirm(BaseSchema):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


class EmailVerifyRequest(BaseSchema):
    token: str


class ResendVerificationRequest(BaseSchema):
    email: EmailStr
    locale: Literal["fr", "en"] = "fr"


class MessageResponse(BaseSchema):
    detail: str


class PasswordResetRequestResponse(BaseSchema):
    detail: str
    # Populated only in debug (no email delivery wired yet); None in production.
    reset_token: str | None = None


class TokenPair(BaseSchema):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class GoogleAuthResponse(TokenPair):
    # Lets the frontend send a brand-new Google signup into onboarding
    # (CV upload) instead of the dashboard, same as a fresh email/password
    # registration -- see AuthService.login_with_google.
    is_new_user: bool


class PublicUser(BaseSchema):
    """Minimal user projection exposed to other modules via the gateway."""

    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
