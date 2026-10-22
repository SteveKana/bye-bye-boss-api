"""Quick compatibility check of the configured OpenAI models.

Run this ON THE SERVER (it needs OPENAI_API_KEY from .env) -- it writes
nothing to the database. For every model setting, it sends one tiny request
to the Responses API with the exact reasoning/verbosity levels production
uses for it, and prints OK or the error OpenAI answers (an unknown model
name, or a parameter the model rejects, shows up here instead of in a real
candidate's analysis).

Cost: 6 calls with a one-word prompt -- a fraction of a cent.

Usage (from the repo root, inside the venv, with .env loaded):
    python -m scripts.check_openai_model
"""

from __future__ import annotations

from openai import OpenAI

from app.core.config import get_settings


def main() -> None:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        print("OPENAI_API_KEY est vide : rien à tester.")
        return
    client = OpenAI(api_key=settings.OPENAI_API_KEY, timeout=120, max_retries=0)

    # (label, model, reasoning effort, verbosity) -- same levels as production.
    checks = [
        ("Analyse CV (cv)", settings.CV_OPENAI_MODEL, "low", "low"),
        ("Pré-tri matching", settings.MATCHING_PREFILTER_MODEL, "low", "low"),
        (
            "Analyse complète matching",
            settings.MATCHING_OPENAI_MODEL,
            "medium",
            "medium",
        ),
        ("Traduction des compétences", settings.MATCHING_LABELS_MODEL, "low", "low"),
        ("Regret Index", settings.REGRET_OPENAI_MODEL, "low", "low"),
        (
            "Adapter mon CV",
            settings.CV_OPTIMIZATION_OPENAI_MODEL,
            "medium",
            "medium",
        ),
    ]
    failures = 0
    for label, model, effort, verbosity in checks:
        try:
            response = client.responses.create(
                model=model,
                input="Réponds uniquement par le mot : ok",
                reasoning={"effort": effort},
                text={"verbosity": verbosity},
            )
            answer = response.output_text.strip()[:30]
            print(f"OK     {label:<28} {model}  ->  {answer!r}")
        except Exception as exc:  # noqa: BLE001 - we want to print any failure
            failures += 1
            print(f"ERREUR {label:<27} {model}  ->  {exc}")
    print()
    if failures:
        print(f"{failures} réglage(s) à corriger avant de basculer.")
    else:
        print("Tout est bon.")


if __name__ == "__main__":
    main()
