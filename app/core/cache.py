"""Pluggable cache: Redis when `REDIS_URL` is set, in-memory otherwise.

    from app.core.cache import cache, cached

    await cache.set("k", "v", ttl=30)
    await cache.get("k")

    @cached(ttl=60)
    async def expensive(x: int) -> int:
        ...

Values are JSON-serialized. The in-memory backend is process-local (fine for
single-worker dev / tests); use Redis for multi-worker deployments.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, TypeVar

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("app.cache")
settings = get_settings()

T = TypeVar("T")


class _MemoryCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float | None, str]] = {}

    async def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, raw = entry
        if expires_at is not None and expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        expires_at = time.monotonic() + ttl if ttl else None
        self._store[key] = (expires_at, json.dumps(value, default=str))

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        self._store.clear()


class _RedisCache:
    def __init__(self, url: str) -> None:
        import redis.asyncio as redis  # local import; optional dependency

        self._redis = redis.from_url(url, decode_responses=True)

    async def get(self, key: str) -> Any | None:
        raw = await self._redis.get(key)
        return json.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        await self._redis.set(key, json.dumps(value, default=str), ex=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def clear(self) -> None:
        await self._redis.flushdb()


def _build_cache():
    if settings.REDIS_URL:
        try:
            logger.info("cache_backend", backend="redis")
            return _RedisCache(settings.REDIS_URL)
        except Exception as exc:  # pragma: no cover - fallback path
            logger.warning("redis_unavailable_using_memory", error=str(exc))
    return _MemoryCache()


cache = _build_cache()


def cached(
    ttl: int | None = None, *, key_prefix: str | None = None
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Cache an async function's result keyed by its positional args."""

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        prefix = key_prefix or f"{func.__module__}.{func.__qualname__}"

        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            key_parts = [prefix, *(str(a) for a in args)]
            key_parts += [f"{k}={v}" for k, v in sorted(kwargs.items())]
            key = ":".join(key_parts)
            hit = await cache.get(key)
            if hit is not None:
                return hit
            result = await func(*args, **kwargs)
            await cache.set(key, result, ttl=ttl or settings.CACHE_DEFAULT_TTL_SECONDS)
            return result

        return wrapper

    return decorator
