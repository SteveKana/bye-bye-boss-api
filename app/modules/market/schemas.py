from __future__ import annotations

import uuid

from app.core.schemas import BaseSchema


class DigestRequest(BaseSchema):
    user_id: uuid.UUID
    # Admin override: send even if the specialty has fewer offers than the
    # threshold (and use the week in progress when no week is complete yet).
    ignore_threshold: bool = False


class DigestPreview(BaseSchema):
    ready: bool
    # Why it is not ready (shown to the admin as is).
    reason: str | None = None
    subject: str | None = None
    html: str | None = None
    label: str | None = None
    offers_analysed: int | None = None
    min_offers: int | None = None
    below_threshold: bool = False


class DigestSent(BaseSchema):
    detail: str


class UnsubscribeInfo(BaseSchema):
    email: str
    already: bool


class UnsubscribeBody(BaseSchema):
    token: str
