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


class UserUpdate(BaseSchema):
    first_name: str | None = Field(default=None, max_length=80)
    last_name: str | None = Field(default=None, max_length=80)


class ChangePasswordRequest(BaseSchema):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseSchema):
    email: EmailStr
    password: str


class RefreshRequest(BaseSchema):
    refresh_token: str


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


class PublicUser(BaseSchema):
    """Minimal user projection exposed to other modules via the gateway."""

    id: uuid.UUID
    email: EmailStr
    first_name: str | None
    last_name: str | None
