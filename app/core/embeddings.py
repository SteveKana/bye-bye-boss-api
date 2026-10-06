"""Text-embedding gateway.

Lives in `app.core` rather than inside a module because it's used by two
different modules that must not depend on each other: `offers` (embeds each
offer at ingestion time) and `matching` (embeds a candidate's CV text, then
ranks the offer pool by cosine similarity -- see
`matching/shortlist.py`). `matching` already depends on `offers` (it reads
the offer pool), so putting this in either module would create a dependency
cycle between them -- see tests/test_architecture.py, which enforces the
module dependency graph stays acyclic. Core infrastructure (like
config/logging) isn't part of that graph, so it's the natural home for
something both sides need.

Same OpenAI vendor/credentials as the rest of the project (OPENAI_API_KEY),
a separate cheap embedding model (EMBEDDING_MODEL) -- unrelated to the
reasoning model used for CV parsing (cv/gateway.py) and matching scoring
(matching/gateway.py). Same call-with-retries pattern as those two gateways,
except a failure here never raises: an embedding is a ranking signal, not
something that should ever block ingesting an offer or scoring a profile,
so a missing embedding just makes that one item sort last (see
matching/shortlist.py) rather than failing the whole run.
"""

from __future__ import annotations

import asyncio

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("core.embeddings")

# text-embedding-3-small's limit is 8191 tokens; ~4 chars/token is a rough
# average for French/English prose, so this is a generous safety margin, not
# a precise token count -- good enough to avoid a wasted API call on an
# unusually long CV or offer description rather than trying to tokenize
# accurately just for this.
_MAX_INPUT_CHARS = 20000


def _embed_sync(*, api_key: str, model: str, timeout: int, text: str) -> list[float]:
    # max_retries=0: see cv/gateway.py and matching/gateway.py -- the SDK's
    # own default retries would otherwise stack with the explicit loop here.
    client = OpenAI(api_key=api_key, timeout=timeout, max_retries=0)
    max_retries = 3
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.embeddings.create(model=model, input=text)
            return response.data[0].embedding
        except (RateLimitError, APITimeoutError, APIError) as exc:
            last_exc = exc
            logger.warning("embedding_retry", attempt=attempt, error=str(exc))
    raise last_exc  # type: ignore[misc]


async def get_embedding(text: str) -> list[float] | None:
    """Best-effort -- never raises. Returns None if no OPENAI_API_KEY is
    configured, the text is empty/blank, or every retry failed."""
    settings = get_settings()
    text = (text or "").strip()
    if not settings.OPENAI_API_KEY or not text:
        return None
    try:
        # Synchronous/blocking OpenAI SDK call -- run off the event loop,
        # same as cv/gateway.structure_cv_text and matching/gateway.analyse_match.
        return await asyncio.to_thread(
            _embed_sync,
            api_key=settings.OPENAI_API_KEY,
            model=settings.EMBEDDING_MODEL,
            timeout=settings.EMBEDDING_TIMEOUT_SECONDS,
            text=text[:_MAX_INPUT_CHARS],
        )
    except Exception as exc:  # noqa: BLE001 -- see docstring: never raises
        logger.warning("embedding_failed", error=str(exc))
        return None
