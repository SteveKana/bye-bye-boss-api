"""Application configuration, loaded from environment / `.env`.

All settings live in a single flat `Settings` object for discoverability.
Access it anywhere via `get_settings()` (cached), never by instantiating
`Settings()` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App -------------------------------------------------------------
    APP_NAME: str = "matchcareer"
    APP_ENV: Literal["local", "test", "staging", "production"] = "local"
    DEBUG: bool = True
    API_VERSION: str = "v1"
    API_PREFIX: str = "/api"  # final prefix becomes /api/v1

    # ---- Database --------------------------------------------------------
    # postgresql+asyncpg://user:pass@host:5432/dbname   (prod/dev)
    # sqlite+aiosqlite:///./app.db                       (quick start)
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"
    DATABASE_ECHO: bool = False
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20
    # Auto create tables on startup (dev convenience). Use Alembic in prod.
    DATABASE_AUTO_CREATE: bool = True

    # ---- Security / JWT --------------------------------------------------
    SECRET_KEY: str = "change-me-in-production-please-32chars-min"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str | None = None
    JWT_AUDIENCE: str | None = None
    ACCESS_TOKEN_TTL_MINUTES: int = 30
    REFRESH_TOKEN_TTL_MINUTES: int = 60 * 24 * 7  # 7 days
    RESET_TOKEN_TTL_MINUTES: int = 30
    BCRYPT_ROUNDS: int = 12

    # Bootstrap admin, seeded at startup if both are set and absent in DB.
    ADMIN_EMAIL: str | None = None
    ADMIN_PASSWORD: str | None = None

    # ---- CORS ------------------------------------------------------------
    CORS_ALLOW_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    CORS_ALLOW_CREDENTIALS: bool = True

    # ---- Cache (Redis optional; falls back to in-memory) -----------------
    REDIS_URL: str | None = None
    CACHE_DEFAULT_TTL_SECONDS: int = 60

    # ---- Rate limiting (uses Redis when set, else in-memory) -------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT_TIMES: int = 100
    RATE_LIMIT_DEFAULT_SECONDS: int = 60

    # ---- Scheduler -------------------------------------------------------
    SCHEDULER_ENABLED: bool = True

    # ---- Logging ---------------------------------------------------------
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False  # True -> JSON logs (prod), False -> pretty console

    @property
    def api_prefix(self) -> str:
        return f"{self.API_PREFIX}/{self.API_VERSION}".replace("//", "/")

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
