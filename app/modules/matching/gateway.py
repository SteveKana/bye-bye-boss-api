"""LLM gateway: scores a CV against a job offer.

Same shape as app/modules/cv/gateway.py (same OpenAI vendor, same
call-with-retries pattern) -- ported from the standalone `matchcareer_engine`
prototype's `llm/client.py`, fixing the same hidden-double-retry issue that
was fixed in cv/gateway.py (see `max_retries=0` below) before it ever shipped
here.

Reasoning effort is "medium", not the "low" used for CV parsing: matching
asks the model to weigh hard/medium/soft blockers and produce two internally
consistent scores (ATS score and ATS potential) -- a heavier judgment task
than filling in a fixed extraction schema, worth the extra latency.
"""

from __future__ import annotations

import asyncio
import json
import re

from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.modules.matching.llm_schema import LLMAnalysis
from app.modules.matching.prompt import build_prompt

logger = get_logger("matching.gateway")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class MatchingUnavailableError(AppError):
    status_code = 503
    code = "matching_unavailable"
    message = "Le calcul de matching n'est pas disponible pour le moment."


class MatchingFailedError(AppError):
    status_code = 502
    code = "matching_failed"
    message = "Le calcul de matching a échoué."


def _extract_json(text: str) -> str:
    """Pull the JSON object out of the model output -- same tolerant parsing
    as cv/gateway.py's _extract_json (clean JSON, ```json fences, or stray
    prose around an outermost {...} span)."""
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

    raise MatchingFailedError()


def _call_openai_sync(*, api_key: str, model: str, timeout: int, prompt: str) -> str:
    # max_retries=0: see app/modules/cv/gateway.py -- the SDK's own default
    # retries would otherwise stack with the explicit retry loop below.
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.create(
                model=model,
                input=prompt,
                reasoning={"effort": "medium"},
                text={"verbosity": "medium"},
            )
            return response.output_text
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_exc = exc
            logger.warning("matching_llm_retry", attempt=attempt, error=str(exc))
    raise MatchingFailedError() from last_exc


async def analyse_match(
    cv_text: str, offer_text: str, *, model: str | None = None
) -> LLMAnalysis:
    """`model` overrides settings.MATCHING_OPENAI_MODEL for this one call --
    every real call site leaves it unset (unchanged behaviour); it exists so
    scripts/compare_matching_models.py can run the identical prompt/parsing/
    validation path against a different model for a side-by-side quality
    check, without duplicating this function."""
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("matching_no_api_key")
        raise MatchingUnavailableError()

    prompt = build_prompt(cv_text, offer_text)

    try:
        # Synchronous/blocking OpenAI SDK call -- run off the event loop, same
        # as cv/gateway.structure_cv_text.
        raw = await asyncio.to_thread(
            _call_openai_sync,
            api_key=settings.OPENAI_API_KEY,
            model=model or settings.MATCHING_OPENAI_MODEL,
            timeout=settings.MATCHING_OPENAI_TIMEOUT_SECONDS,
            prompt=prompt,
        )
    except MatchingFailedError:
        raise

    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.error("matching_bad_json", raw=raw)
        raise MatchingFailedError() from exc

    try:
        return LLMAnalysis.model_validate(data)
    except ValidationError as exc:
        # `raw` alone (the previous behaviour) meant diagnosing a schema
        # drift required manually reconstructing the payload and replaying
        # it through LLMAnalysis by hand to even find which field failed --
        # exactly what the cv_skills incident (2026-09-21) took. Logging the
        # error itself names the offending field(s) and why, right in this
        # line, the moment it happens.
        logger.error("matching_bad_schema", error=str(exc), raw=raw)
        raise MatchingFailedError() from exc
