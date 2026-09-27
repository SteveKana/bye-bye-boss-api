from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime
from sqlmodel import Field

from app.core.models import BaseModel


class SubscriptionPlan(enum.StrEnum):
    standard = "standard"
    premium = "premium"


class User(BaseModel, table=True):
    __tablename__ = "users"

    email: str = Field(index=True, unique=True, nullable=False)
    # Nullable: an account created via "Continuer avec Google" (see
    # AuthService.login_with_google) has no password at all until the user
    # sets one through the normal reset-password flow -- Google already
    # vouches for the email, so there's nothing to hash at signup time.
    # core/security.verify_password and AuthService.authenticate both guard
    # against None here rather than erroring.
    password_hash: str | None = Field(default=None, nullable=True)
    first_name: str | None = Field(default=None)
    last_name: str | None = Field(default=None)
    is_active: bool = Field(default=True, nullable=False)
    is_verified: bool = Field(default=False, nullable=False)
    isadmin: bool = Field(default=False, nullable=False)

    # Google's own, stable user id ("sub" claim) for an account created or
    # linked via "Continuer avec Google" -- kept alongside the email match
    # so the frontend can show "connecté via Google" and so a future email
    # change on the Google side doesn't orphan the link. Unique but nullable
    # (most users never sign in with Google at all).
    google_id: str | None = Field(default=None, index=True, unique=True, nullable=True)

    # Subscription plan — gates the manual-rescore frequency bypass (spec §6).
    # Stored as a plain string; allowed values live in `SubscriptionPlan`.
    subscription: str = Field(default=SubscriptionPlan.standard.value, nullable=False)

    # Timestamp of the last manual rescore — reference for the 24h sliding window.
    last_rescoring_time: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        nullable=True,
    )
