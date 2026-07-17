from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from app.core.schemas import BaseSchema


class UserCreate(BaseSchema):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = None


class UserRead(BaseSchema):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    isadmin: bool
    subscription: str
    last_rescoring_time: datetime | None
    created_at: datetime


class LoginRequest(BaseSchema):
    email: EmailStr
    password: str


class RefreshRequest(BaseSchema):
    refresh_token: str


class PasswordResetRequest(BaseSchema):
    email: EmailStr


class PasswordResetConfirm(BaseSchema):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


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
    full_name: str | None
