from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field

from app.core.schemas import BaseSchema


class LeadCreate(BaseSchema):
    email: EmailStr
    locale: Literal["fr", "en"] = "fr"
    source: str | None = Field(default=None, max_length=120)


class LeadRead(BaseSchema):
    id: uuid.UUID
    email: EmailStr
    locale: str
    source: str | None
    created_at: datetime


class LeadCaptureResponse(BaseSchema):
    """Uniform response: the same body whether or not the email was already on
    the list, so the endpoint cannot be used to probe who signed up."""

    detail: str
