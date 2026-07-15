"""Fixed-window rate limiting, used as a FastAPI dependency.

Backend mirrors the cache: atomic counters in Redis when `REDIS_URL` is set,
otherwise a process-local in-memory window (fine for single-worker dev/tests;
use Redis for multi-worker deployments).

Apply to a single route:

    from fastapi import Depends
    from app.core.ratelimit import RateLimiter

    @router.post("/login", dependencies=[Depends(RateLimiter(times=10, seconds=60))])
    async def login(...): ...

...or to a whole router (every route shares the budget under that scope):

    router = APIRouter(dependencies=[Depends(RateLimiter(times=100, seconds=60))])

On success the response carries `X-RateLimit-Limit/Remaining/Reset`; when the
budget is exceeded a 429 is raised with a `Retry-After` header. Globally
toggled by `RATE_LIMIT_ENABLED`; callers falling back to the configured
`RATE_LIMIT_DEFAULT_*` when `times`/`seconds` are omitted.
"""

from __future__ import annotations

import time

from fastapi import Request, Response

from app.core.config import get_settings
from app.core.exceptions import TooManyRequestsError
from app.core.logging import get_logger

logger = get_logger("app.ratelimit")


class _MemoryBackend:
    def __init__(self) -> None:
        self._store: dict[str, tuple[int, float]] = {}

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        now = time.monotonic()
        count, end = self._store.get(key, (0, 0.0))
        if now >= end:
            count, end = 0, now + window
        count += 1
        self._store[key] = (count, end)
        return count, max(int(end - now), 1)

    async def reset(self) -> None:
        self._store.clear()


class _RedisBackend:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # optional dependency

        self._redis = redis.from_url(url, decode_responses=True)

    async def hit(self, key: str, window: int) -> tuple[int, int]:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        if count == 1 or ttl < 0:
            await self._redis.expire(key, window)
            ttl = window
        return int(count), max(int(ttl), 1)

    async def reset(self) -> None:
        await self._redis.flushdb()


def _build_backend():
    settings = get_settings()
    if settings.REDIS_URL:
        try:
            logger.info("ratelimit_backend", backend="redis")
            return _RedisBackend(settings.REDIS_URL)
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning("redis_unavailable_using_memory", error=str(exc))
    return _MemoryBackend()


backend = _build_backend()


def _client_id(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimiter:
    def __init__(
        self,
        times: int | None = None,
        seconds: int | None = None,
        *,
        scope: str | None = None,
    ) -> None:
        settings = get_settings()
        self.times = times or settings.RATE_LIMIT_DEFAULT_TIMES
        self.seconds = seconds or settings.RATE_LIMIT_DEFAULT_SECONDS
        self.scope = scope

    async def __call__(self, request: Request, response: Response) -> None:
        if not get_settings().RATE_LIMIT_ENABLED:
            return

        route = request.scope.get("route")
        scope = self.scope or getattr(route, "path", None) or request.url.path
        key = f"rl:{scope}:{_client_id(request)}"

        count, reset = await backend.hit(key, self.seconds)
        remaining = max(self.times - count, 0)
        response.headers["X-RateLimit-Limit"] = str(self.times)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)

        if count > self.times:
            raise TooManyRequestsError(
                "Rate limit exceeded. Please retry later.",
                details={"retry_after": reset},
                headers={
                    "Retry-After": str(reset),
                    "X-RateLimit-Limit": str(self.times),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                },
            )
