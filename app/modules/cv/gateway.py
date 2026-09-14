"""LLM gateway: turns raw CV text into a structured candidate profile.

Isolated behind this module so swapping providers (or models) later touches
one file. Uses the OpenAI Responses API — same provider and call shape as
`matchcareer_engine` (the standalone matching engine), so the whole project
stays on one LLM vendor. A failed call or a malformed JSON body raises — the
route turns that into a clear error rather than saving garbage.
"""

from __future__ import annotations

import asyncio
import json
import re

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger

logger = get_logger("cv.gateway")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_PROMPT_TEMPLATE = """Tu extrais les informations d'un CV en français vers un \
objet JSON strict. Réponds UNIQUEMENT avec du JSON valide, sans texte autour, \
sans balises markdown. Si une information est absente du CV, mets une chaîne \
vide "" (ou une liste vide []) — n'invente jamais de donnée.

Schéma exact à respecter :
{{
  "first_name": "",
  "last_name": "",
  "email": "",
  "location": "",
  "total_experience": "",
  "experiences": [
    {{"title": "", "company": "", "period": "", "description": "", "tools": []}}
  ],
  "skills": [],
  "formations": [{{"title": "", "school_period": ""}}],
  "languages": [{{"name": "", "level": ""}}],
  "certifications": [{{"title": "", "issuer_period": ""}}]
}}

CV

{cv}
"""


class CvParsingUnavailableError(AppError):
    status_code = 503
    code = "cv_parsing_unavailable"
    message = "L'analyse automatique du CV n'est pas disponible pour le moment."


class CvParsingFailedError(AppError):
    status_code = 502
    code = "cv_parsing_failed"
    message = "L'analyse du CV a échoué. Merci de réessayer."


def _extract_json(text: str) -> str:
    """Pull the JSON object out of the model output.

    Handles: clean JSON, JSON wrapped in ```json fences, or JSON with stray
    prose around it (falls back to the outermost {...} span).
    """
    text = text.strip()

    fenced = _JSON_FENCE.search(text)
    if fenced:
        return fenced.group(1).strip()

    if text.startswith("{") and text.endswith("}"):
        return text

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    raise CvParsingFailedError()


def _call_openai_sync(*, api_key: str, model: str, timeout: int, prompt: str) -> str:
    client = OpenAI(api_key=api_key, timeout=timeout)
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.create(model=model, input=prompt)
            return response.output_text
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_exc = exc
            logger.warning("cv_parsing_retry", attempt=attempt, error=str(exc))
    raise CvParsingFailedError() from last_exc


async def structure_cv_text(raw_text: str) -> dict:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("cv_parsing_no_api_key")
        raise CvParsingUnavailableError()

    prompt = _PROMPT_TEMPLATE.format(cv=raw_text)

    try:
        # The OpenAI SDK call is synchronous/blocking — run it off the event
        # loop so it doesn't stall other requests.
        raw = await asyncio.to_thread(
            _call_openai_sync,
            api_key=settings.OPENAI_API_KEY,
            model=settings.OPENAI_MODEL,
            timeout=settings.OPENAI_TIMEOUT_SECONDS,
            prompt=prompt,
        )
    except CvParsingFailedError:
        raise

    payload = _extract_json(raw)
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.error("cv_parsing_bad_json", raw=raw)
        raise CvParsingFailedError() from exc
