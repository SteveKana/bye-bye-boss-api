"""LLM gateway: turns raw CV text into a structured candidate profile.

Isolated behind this module so swapping providers (or models) later touches
one file. A non-2xx response or a malformed JSON body raises — the route
turns that into a clear error rather than saving garbage.
"""

from __future__ import annotations

import json

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger("cv.gateway")

_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"

_SYSTEM_PROMPT = """Tu extrais les informations d'un CV en français vers un \
objet JSON strict. Réponds UNIQUEMENT avec du JSON valide, sans texte autour, \
sans balises markdown. Si une information est absente du CV, mets une chaîne \
vide "" (ou une liste vide []) — n'invente jamais de donnée.

Schéma exact à respecter :
{
  "first_name": "",
  "last_name": "",
  "email": "",
  "location": "",
  "availability": "",
  "total_experience": "",
  "experiences": [
    {"title": "", "company": "", "period": "", "description": "", "tools": []}
  ],
  "skills": [],
  "formations": [{"title": "", "school_period": ""}],
  "languages": [{"name": "", "level": ""}],
  "certifications": [{"title": "", "issuer_period": ""}]
}"""


class CvParsingUnavailableError(AppError):
    status_code = 503
    code = "cv_parsing_unavailable"
    message = "L'analyse automatique du CV n'est pas disponible pour le moment."


class CvParsingFailedError(AppError):
    status_code = 502
    code = "cv_parsing_failed"
    message = "L'analyse du CV a échoué. Merci de réessayer."


async def structure_cv_text(raw_text: str) -> dict:
    settings = get_settings()
    if not settings.ANTHROPIC_API_KEY:
        logger.warning("cv_parsing_no_api_key")
        raise CvParsingUnavailableError()

    payload = {
        "model": settings.ANTHROPIC_MODEL,
        "max_tokens": 4000,
        "system": _SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Voici le texte brut extrait du CV :\n\n{raw_text}",
            }
        ],
    }
    headers = {
        "x-api-key": settings.ANTHROPIC_API_KEY,
        "anthropic-version": _ANTHROPIC_VERSION,
        "content-type": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.ANTHROPIC_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(_ANTHROPIC_URL, json=payload, headers=headers)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("cv_parsing_http_error", error=str(exc))
            raise CvParsingFailedError() from exc

    body = response.json()
    try:
        text_block = next(
            block["text"] for block in body["content"] if block["type"] == "text"
        )
        return json.loads(text_block)
    except (KeyError, StopIteration, json.JSONDecodeError) as exc:
        logger.error("cv_parsing_bad_response", body=body)
        raise CvParsingFailedError() from exc
