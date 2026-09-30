"""LLM call that turns a set of Reddit mentions into a Regret Index --
same shape as matching/gateway.py (same OpenAI vendor, same
call-with-retries/JSON-extraction pattern), kept as its own small file
rather than folded into gateway.py since it has its own prompt, schema and
settings (see regret_prompt.py, regret_schema.py, REGRET_OPENAI_MODEL)."""

from __future__ import annotations

import asyncio
import json

from openai import APIError, APITimeoutError, OpenAI, RateLimitError
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger

# Reusing gateway.py's tolerant JSON extraction rather than duplicating it --
# both live in this module, so the leading underscore is a same-package
# convenience import, not a layering violation.
from app.modules.matching.gateway import MatchingFailedError, _extract_json
from app.modules.matching.regret_prompt import build_regret_prompt
from app.modules.matching.regret_schema import RegretAnalysis

logger = get_logger("matching.regret_gateway")


def _call_openai_sync(*, api_key: str, model: str, timeout: int, prompt: str) -> str:
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    max_retries = 2
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.responses.create(
                model=model,
                input=prompt,
                reasoning={"effort": "low"},
                text={"verbosity": "low"},
            )
            return response.output_text
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_exc = exc
            logger.warning("regret_llm_retry", attempt=attempt, error=str(exc))
    raise MatchingFailedError() from last_exc


async def analyse_regret(
    company_name: str, mentions: list[dict]
) -> RegretAnalysis | None:
    """None on any failure (no API key, LLM error, bad JSON/schema) -- the
    caller (regret_service.py) treats that exactly like "insufficient": never
    fabricate a score just because the LLM call itself broke."""
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("regret_no_api_key")
        return None

    prompt = build_regret_prompt(company_name, mentions)

    try:
        raw = await asyncio.to_thread(
            _call_openai_sync,
            api_key=settings.OPENAI_API_KEY,
            model=settings.REGRET_OPENAI_MODEL,
            timeout=settings.REGRET_OPENAI_TIMEOUT_SECONDS,
            prompt=prompt,
        )
    except MatchingFailedError as exc:
        logger.warning("regret_llm_failed", company=company_name, error=str(exc))
        return None

    try:
        payload = _extract_json(raw)
        data = json.loads(payload)
        return RegretAnalysis.model_validate(data)
    except (MatchingFailedError, json.JSONDecodeError, ValidationError) as exc:
        logger.warning("regret_llm_bad_output", company=company_name, error=str(exc))
        return None
