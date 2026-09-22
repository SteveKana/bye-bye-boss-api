"""LLM gateway for CV optimization: rewrites a candidate's CV for one
specific offer.

Same shape as matching/gateway.py (same OpenAI vendor, same
call-with-retries/JSON-extraction pattern, same `max_retries=0` fix) --
duplicated rather than shared, matching this codebase's existing convention
of one small gateway per LLM call site (see gateway.py's own "ported from
the standalone prototype" precedent) instead of a shared abstraction the
project doesn't otherwise have.
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
from app.modules.matching.cv_optimization_prompt import build_prompt
from app.modules.matching.cv_optimization_schema import CVOptimizationResult

logger = get_logger("matching.cv_optimization_gateway")

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


class CVOptimizationUnavailableError(AppError):
    status_code = 503
    code = "cv_optimization_unavailable"
    message = "L'optimisation de CV n'est pas disponible pour le moment."


class CVOptimizationFailedError(AppError):
    status_code = 502
    code = "cv_optimization_failed"
    message = "L'optimisation de CV a échoué."


def _extract_json(text: str) -> str:
    """Same tolerant parsing as matching/gateway.py's _extract_json."""
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

    raise CVOptimizationFailedError()


def _call_openai_sync(*, api_key: str, model: str, timeout: int, prompt: str) -> str:
    # max_retries=0: the SDK's own default retries would otherwise stack
    # with the explicit retry loop below -- see matching/gateway.py.
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
            logger.warning("cv_optimization_llm_retry", attempt=attempt, error=str(exc))
    raise CVOptimizationFailedError() from last_exc


async def optimize_cv(
    cv: dict, offer_text: str, analysis: dict, *, model: str | None = None
) -> CVOptimizationResult:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("cv_optimization_no_api_key")
        raise CVOptimizationUnavailableError()

    prompt = build_prompt(cv, offer_text, analysis)

    try:
        raw = await asyncio.to_thread(
            _call_openai_sync,
            api_key=settings.OPENAI_API_KEY,
            model=model or settings.CV_OPTIMIZATION_OPENAI_MODEL,
            timeout=settings.CV_OPTIMIZATION_OPENAI_TIMEOUT_SECONDS,
            prompt=prompt,
        )
    except CVOptimizationFailedError:
        raise

    payload = _extract_json(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.error("cv_optimization_bad_json", raw=raw)
        raise CVOptimizationFailedError() from exc

    try:
        return CVOptimizationResult.model_validate(data)
    except ValidationError as exc:
        logger.error("cv_optimization_bad_schema", error=str(exc), raw=raw)
        raise CVOptimizationFailedError() from exc
