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
    password_hash: str = Field(nullable=False)
    full_name: str | None = Field(default=None)
    is_active: bool = Field(default=True, nullable=False)
    isadmin: bool = Field(default=False, nullable=False)

    # Subscription plan — gates the manual-rescore frequency bypass (spec §6).
    # Stored as a plain string; allowed values live in `SubscriptionPlan`.
    subscription: str = Field(default=SubscriptionPlan.standard.value, nullable=False)

    # Timestamp of the last manual rescore — reference for the 24h sliding window.
    last_rescoring_time: datetime | None = Field(
        default=None,
        sa_type=DateTime(timezone=True),
        nullable=True,
    )
