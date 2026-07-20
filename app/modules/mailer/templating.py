"""Mail templating.

A module owning an email keeps its templates next to its code:

    <module>/templates/mail/<name>/<locale>/subject.txt
                                          /body.txt
                                          /body.html

`body.html` may extend the shared layout provided here (`base.html`), so every
email looks the same without duplicating markup. Rendering is generic: the
mailer knows how to render and send, the feature module owns the wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from jinja2 import (
    ChoiceLoader,
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    select_autoescape,
)

from app.core.config import get_settings

SHARED_TEMPLATES = Path(__file__).parent / "templates"
DEFAULT_LOCALE = "fr"


@dataclass(frozen=True)
class RenderedMail:
    subject: str
    text: str
    html: str | None = None


@lru_cache
def _environment(templates_dir: Path) -> Environment:
    return Environment(
        loader=ChoiceLoader(
            [FileSystemLoader(templates_dir), FileSystemLoader(SHARED_TEMPLATES)]
        ),
        # Only escape HTML: the plain-text part must stay verbatim.
        autoescape=select_autoescape(enabled_extensions=("html",), default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def _resolve_locale(templates_dir: Path, name: str, locale: str) -> str:
    if (templates_dir / "mail" / name / locale).is_dir():
        return locale
    return DEFAULT_LOCALE


def render_mail(
    templates_dir: Path, name: str, locale: str, **context: object
) -> RenderedMail:
    """Render one email in the requested locale (falling back to the default).

    `templates_dir` is the calling module's `templates` directory.
    """
    settings = get_settings()
    env = _environment(templates_dir)
    resolved = _resolve_locale(templates_dir, name, locale)
    base = f"mail/{name}/{resolved}"

    ctx = {
        "brand": settings.EMAIL_FROM_NAME,
        "locale": resolved,
        **context,
    }

    subject = env.get_template(f"{base}/subject.txt").render(ctx).strip()
    text = env.get_template(f"{base}/body.txt").render(ctx)

    # The HTML part is optional, but a broken template must still surface:
    # only a missing file is tolerated here.
    try:
        html: str | None = env.get_template(f"{base}/body.html").render(ctx)
    except TemplateNotFound:
        html = None

    return RenderedMail(subject=subject, text=text, html=html)
