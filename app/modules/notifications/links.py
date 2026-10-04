"""The link every notification carries to switch notifications off.

Points at the "Notifications" card of the Paramètres page (where the
email / Discord / WhatsApp switches live). Not a one-click unsubscribe: the
candidate lands on the page (after logging in if needed) and switches the
channel off themselves.
"""

from __future__ import annotations

from app.core.config import get_settings

SETTINGS_LINK_LABEL = "Désactiver ces notifications"


def notification_settings_url() -> str:
    return f"{get_settings().APP_URL.rstrip('/')}/settings#notifications"
