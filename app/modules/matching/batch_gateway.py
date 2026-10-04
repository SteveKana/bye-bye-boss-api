"""Thin wrapper around OpenAI's Batch API (2026-10-04 cost redesign: -50% on
every token, results guaranteed within 24h but not instantly).

Same vendor/key/`max_retries=0` convention as gateway.py and cv/gateway.py.
Every function here is synchronous SDK code run off the event loop through
`asyncio.to_thread`, like the rest of the matching gateways.

NOT verified against the live Batch API from the development sandbox (no
OpenAI key there) -- the request/response shapes below follow OpenAI's
documented format for the `/v1/responses` endpoint (one JSON line per
request: custom_id/method/url/body; results come back one JSON line per
request with `response.body` being a regular Responses API object). The
unit tests exercise the whole pipeline against a fake client; the first real
run should be watched.
"""

from __future__ import annotations

import asyncio
import io
import json
from dataclasses import dataclass
from typing import Any

from openai import APIError, OpenAI

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.matching.gateway import MatchingFailedError, MatchingUnavailableError

logger = get_logger("matching.batch_gateway")

ENDPOINT = "/v1/responses"
COMPLETION_WINDOW = "24h"

# OpenAI batch statuses that mean "will never produce (more) results".
TERMINAL_FAILURE_STATUSES = {"failed", "expired", "cancelled"}


@dataclass
class BatchInfo:
    status: str
    output_file_id: str | None = None


def _client() -> OpenAI:
    settings = get_settings()
    if not settings.OPENAI_API_KEY:
        logger.warning("matching_no_api_key")
        raise MatchingUnavailableError()
    return OpenAI(
        api_key=settings.OPENAI_API_KEY,
        timeout=settings.MATCHING_OPENAI_TIMEOUT_SECONDS,
        max_retries=0,
    )


def build_request(
    *, custom_id: str, model: str, prompt: str, effort: str, verbosity: str
) -> dict[str, Any]:
    """One line of the batch input file. Same call parameters as the direct
    `client.responses.create(...)` in gateway.py, apart from the model and
    the reasoning/verbosity levels (lighter for the pre-filter)."""
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": ENDPOINT,
        "body": {
            "model": model,
            "input": prompt,
            "reasoning": {"effort": effort},
            "text": {"verbosity": verbosity},
        },
    }


def _submit_sync(requests: list[dict[str, Any]]) -> str:
    client = _client()
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in requests)
    try:
        uploaded = client.files.create(
            file=("matching_batch.jsonl", io.BytesIO(payload.encode("utf-8"))),
            purpose="batch",
        )
        batch = client.batches.create(
            input_file_id=uploaded.id,
            endpoint=ENDPOINT,  # type: ignore[arg-type]
            completion_window=COMPLETION_WINDOW,  # type: ignore[arg-type]
        )
    except APIError as exc:
        logger.error("matching_batch_submit_failed", error=str(exc))
        raise MatchingFailedError() from exc
    return batch.id


async def submit_batch(requests: list[dict[str, Any]]) -> str:
    """Upload the requests and start the batch; returns OpenAI's batch id."""
    return await asyncio.to_thread(_submit_sync, requests)


def _status_sync(openai_batch_id: str) -> BatchInfo:
    client = _client()
    try:
        batch = client.batches.retrieve(openai_batch_id)
    except APIError as exc:
        logger.warning(
            "matching_batch_status_failed", batch=openai_batch_id, error=str(exc)
        )
        raise MatchingFailedError() from exc
    return BatchInfo(status=batch.status, output_file_id=batch.output_file_id)


async def get_batch_info(openai_batch_id: str) -> BatchInfo:
    return await asyncio.to_thread(_status_sync, openai_batch_id)


def extract_output_text(body: dict[str, Any]) -> str | None:
    """Plain text of a Responses API object given as raw JSON (the SDK's
    `output_text` convenience property doesn't exist on a bare dict): every
    `output_text` part of every `message` item, concatenated. None when the
    model produced no text at all."""
    parts: list[str] = []
    for item in body.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    return "".join(parts) or None


def parse_results(jsonl: str) -> dict[str, str | None]:
    """custom_id -> model output text; None for a request that errored or
    produced no text (the caller retries those)."""
    results: dict[str, str | None] = {}
    for line in jsonl.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("matching_batch_unreadable_result_line")
            continue
        custom_id = entry.get("custom_id")
        if not custom_id:
            continue
        response = entry.get("response") or {}
        if entry.get("error") or response.get("status_code") != 200:
            results[custom_id] = None
            continue
        results[custom_id] = extract_output_text(response.get("body") or {})
    return results


def _download_sync(file_id: str) -> str:
    client = _client()
    try:
        content = client.files.content(file_id)
    except APIError as exc:
        logger.warning("matching_batch_download_failed", file=file_id, error=str(exc))
        raise MatchingFailedError() from exc
    return content.text


async def download_results(file_id: str) -> dict[str, str | None]:
    return parse_results(await asyncio.to_thread(_download_sync, file_id))
