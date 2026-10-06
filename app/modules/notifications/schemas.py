from __future__ import annotations

from app.core.schemas import BaseSchema


class NotificationPreferenceRead(BaseSchema):
    email_enabled: bool
    discord_enabled: bool
    discord_webhook_url: str | None
    whatsapp_enabled: bool
    whatsapp_phone_number: str | None


class NotificationPreferenceUpdate(BaseSchema):
    """All fields optional -- a PUT only changes what it sends, same
    `exclude_unset` pattern as the rest of the app's partial updates.
    Cross-field checks (e.g. discord_enabled=True needs a webhook URL) live
    in NotificationPreferenceService, not here, so the error goes through
    the app's normal AppError -> JSON envelope rather than a raw 422."""

    email_enabled: bool | None = None
    discord_enabled: bool | None = None
    discord_webhook_url: str | None = None
    whatsapp_enabled: bool | None = None
    whatsapp_phone_number: str | None = None


class NotificationTestSendResult(BaseSchema):
    """Which channels a POST /preferences/test-send actually delivered to --
    e.g. ["email"] if Discord is configured but its webhook rejected the
    call, or ["email", "discord"] once both work."""

    channels_sent: list[str]
