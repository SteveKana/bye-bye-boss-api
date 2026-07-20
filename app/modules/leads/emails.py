"""Acknowledgement mail sent when someone joins the waitlist."""

from __future__ import annotations

BRAND = "Bye Bye Boss"

_ACK = {
    "fr": {
        "subject": f"Bienvenue sur la liste d'attente {BRAND}",
        "text": (
            "Bonjour,\n\n"
            f"Votre inscription à la liste d'attente {BRAND} est bien enregistrée.\n"
            "Vous serez prévenu dès l'ouverture, et vous bénéficiez d'un accès "
            "prioritaire au lancement.\n\n"
            "Nous ne vous enverrons rien d'autre d'ici là.\n\n"
            f"À très vite,\nL'équipe {BRAND}"
        ),
    },
    "en": {
        "subject": f"Welcome to the {BRAND} waitlist",
        "text": (
            "Hi,\n\n"
            f"Your spot on the {BRAND} waitlist is confirmed.\n"
            "We'll let you know as soon as we open, and you get priority access "
            "at launch.\n\n"
            "We won't send you anything else until then.\n\n"
            f"See you soon,\nThe {BRAND} team"
        ),
    },
}


def build_ack_email(locale: str) -> tuple[str, str]:
    """Return (subject, text) for the given locale, falling back to French."""
    content = _ACK.get(locale, _ACK["fr"])
    return content["subject"], content["text"]
