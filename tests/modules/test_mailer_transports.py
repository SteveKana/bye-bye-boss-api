from __future__ import annotations

import httpx
import pytest

from app.core.config import get_settings
from app.modules.mailer.transports import active_provider, is_configured, send_email


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch):
    """The settings object is a cached singleton: patch attributes on it."""
    current = get_settings()
    monkeypatch.setattr(current, "EMAIL_FROM", "contact@byebyeboss.fr")
    monkeypatch.setattr(current, "EMAIL_FROM_NAME", "Bye Bye Boss")
    return current


async def test_smtp_is_the_default_provider(settings) -> None:
    assert active_provider() == "smtp"


async def test_unconfigured_provider_only_logs(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAIL_PROVIDER", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    assert is_configured() is False
    # Must not raise: the message is logged, the queue moves on.
    await send_email("someone@example.com", "Sujet", "corps")


async def test_mailgun_is_configured_only_with_key_and_domain(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAIL_PROVIDER", "mailgun")
    monkeypatch.setattr(settings, "MAILGUN_SECRET", "key-123")
    monkeypatch.setattr(settings, "MAILGUN_DOMAIN", None)
    assert is_configured() is False

    monkeypatch.setattr(settings, "MAILGUN_DOMAIN", "mg.byebyeboss.fr")
    assert is_configured() is True


async def test_mailgun_posts_the_message_to_the_api(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAIL_PROVIDER", "mailgun")
    monkeypatch.setattr(settings, "MAILGUN_SECRET", "key-123")
    monkeypatch.setattr(settings, "MAILGUN_DOMAIN", "mg.byebyeboss.fr")
    monkeypatch.setattr(settings, "MAILGUN_ENDPOINT", "https://api.eu.mailgun.net")

    captured: dict = {}

    async def fake_post(self, url, **kwargs):  # noqa: ANN001
        captured["url"] = url
        captured["auth"] = kwargs.get("auth")
        captured["data"] = kwargs.get("data")
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    await send_email("lead@example.com", "Bienvenue", "corps texte", "<p>corps</p>")

    assert captured["url"] == "https://api.eu.mailgun.net/v3/mg.byebyeboss.fr/messages"
    assert captured["auth"] == ("api", "key-123")
    assert captured["data"]["to"] == "lead@example.com"
    assert captured["data"]["subject"] == "Bienvenue"
    assert captured["data"]["from"] == "Bye Bye Boss <contact@byebyeboss.fr>"
    assert captured["data"]["html"] == "<p>corps</p>"


async def test_endpoint_accepts_a_bare_host(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Mailgun's dashboard shows the endpoint without a scheme.
    monkeypatch.setattr(settings, "MAILGUN_ENDPOINT", "api.eu.mailgun.net")
    assert settings.mailgun_base_url == "https://api.eu.mailgun.net"

    monkeypatch.setattr(settings, "MAILGUN_ENDPOINT", "https://api.mailgun.net/")
    assert settings.mailgun_base_url == "https://api.mailgun.net"


async def test_mailgun_error_raises_so_the_queue_retries(
    settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "MAIL_PROVIDER", "mailgun")
    monkeypatch.setattr(settings, "MAILGUN_SECRET", "key-123")
    monkeypatch.setattr(settings, "MAILGUN_DOMAIN", "mg.byebyeboss.fr")

    async def failing_post(self, url, **kwargs):  # noqa: ANN001
        return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post)

    with pytest.raises(httpx.HTTPStatusError):
        await send_email("lead@example.com", "Bienvenue", "corps")
