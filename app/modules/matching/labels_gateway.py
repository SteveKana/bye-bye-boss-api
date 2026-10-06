"""LLM gateway for the skill-label glossary: turns internal skill concepts
("roadmap_planning") into the French wording a candidate expects to read
("Planification de roadmap").

Same shape as cv_optimization_gateway.py (one small OpenAI call per chunk,
explicit retries, `max_retries=0`). Deliberately forgiving: a chunk that
keeps failing is simply skipped -- the caller falls back to the plain
underscore-free label and retries on its next run, so a hiccup here can
never affect the matching itself.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.matching.skill_labels import clean_skill_label

logger = get_logger("matching.labels_gateway")

CHUNK_SIZE = 150
MAX_PARALLEL_CHUNKS = 4

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

PROMPT_TEMPLATE = """
Tu reçois une liste d'identifiants techniques de compétences, d'outils ou de
responsabilités extraits d'offres d'emploi et de CV (souvent en anglais, avec des
underscores, ex. "roadmap_planning").

Pour chacun, écris le libellé en FRANÇAIS NATUREL tel qu'on le lirait dans une
offre d'emploi ou un CV :
- court (1 à 4 mots), sans underscore, première lettre en majuscule ;
- les noms d'outils, de méthodes, de langages et les acronymes restent tels quels
  (SQL, Jira, Scrum, Python, API REST, UX...) ;
- ne change pas le sens, ne rajoute rien.

Exemples :
"requirements_gathering" -> "Recueil du besoin"
"workshop_facilitation" -> "Animation d'ateliers"
"user_story" -> "User stories"
"backlog_management" -> "Gestion du backlog"
"agile" -> "Agile"
"sql" -> "SQL"

Retourne UNIQUEMENT du JSON valide, sans texte avant ou après, sans balises Markdown :
{{"labels": {{"<identifiant>": "<libellé français>"}}}}

IDENTIFIANTS
{keys}
"""


def build_prompt(keys: Sequence[str]) -> str:
    return PROMPT_TEMPLATE.format(keys=json.dumps(list(keys), ensure_ascii=False))


def parse_labels(raw: str, keys: Sequence[str]) -> dict[str, str]:
    """The labels the model returned for the keys we asked about -- anything
    else (unknown keys, empty or non-string values) is dropped."""
    text = raw.strip()
    fenced = _JSON_FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return {}
    labels = data.get("labels") if isinstance(data, dict) else None
    if not isinstance(labels, dict):
        return {}
    wanted = set(keys)
    result: dict[str, str] = {}
    for key, label in labels.items():
        if key in wanted and isinstance(label, str) and label.strip():
            result[key] = clean_skill_label(label)
    return result


def _call_openai_sync(*, api_key: str, model: str, timeout: int, prompt: str) -> str:
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    max_retries = 3
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
            logger.warning("skill_labels_llm_retry", attempt=attempt, error=str(exc))
    raise RuntimeError("skill labels: OpenAI call failed") from last_exc


async def _translate_chunk(keys: Sequence[str]) -> dict[str, str] | None:
    """None when the chunk failed (retry later); otherwise one label per key
    -- a key the model forgot gets the plain underscore-free fallback, so a
    single stubborn identifier can never make a chunk retry forever."""
    settings = get_settings()
    try:
        raw = await asyncio.to_thread(
            _call_openai_sync,
            api_key=settings.OPENAI_API_KEY or "",
            model=settings.MATCHING_LABELS_MODEL,
            timeout=settings.MATCHING_LABELS_TIMEOUT_SECONDS,
            prompt=build_prompt(keys),
        )
    except RuntimeError:
        return None
    labels = parse_labels(raw, keys)
    if not labels:
        logger.error("skill_labels_bad_answer", raw=raw[:500])
        return None
    return {key: labels.get(key) or clean_skill_label(key) for key in keys}


async def translate_keys(keys: Sequence[str]) -> dict[str, str]:
    """French labels for `keys`, chunk by chunk (a few in parallel).

    Always returns a dict with the keys that were translated -- a chunk that
    failed (or no API key at all) is simply absent from it, and the caller
    tries those keys again on its next run.
    """
    if not keys:
        return {}
    if not get_settings().OPENAI_API_KEY:
        logger.warning("skill_labels_no_api_key")
        return {}

    chunks = [list(keys[i : i + CHUNK_SIZE]) for i in range(0, len(keys), CHUNK_SIZE)]
    gate = asyncio.Semaphore(MAX_PARALLEL_CHUNKS)

    async def run(chunk: list[str]) -> dict[str, str] | None:
        async with gate:
            return await _translate_chunk(chunk)

    results = await asyncio.gather(*(run(c) for c in chunks))
    merged: dict[str, str] = {}
    for part in results:
        if part:
            merged.update(part)
    return merged
