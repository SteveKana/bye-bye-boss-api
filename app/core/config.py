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
    APP_NAME: str = "byebyeboss"
    APP_ENV: Literal["local", "test", "staging", "production"] = "local"
    DEBUG: bool = True
    API_VERSION: str = "v1"
    API_PREFIX: str = "/api"  # final prefix becomes /api/v1
    # Public base URL of the frontend, used to build links in emails.
    APP_URL: str = "http://localhost:3000"

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
    VERIFY_TOKEN_TTL_MINUTES: int = 60 * 24  # 1 day
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

    # ---- Mail ------------------------------------------------------------
    # Outgoing mail is queued in the `mailer` module and flushed by a scheduled
    # worker. The transport is pluggable: when the selected provider is not
    # configured the message is only logged (dev mode), so the whole flow stays
    # testable before credentials exist.
    MAIL_PROVIDER: Literal["smtp", "mailgun"] = "smtp"

    # -- Mailgun (HTTP API)
    MAILGUN_SECRET: str | None = None
    MAILGUN_DOMAIN: str | None = None
    # Host or full URL. EU accounts: api.eu.mailgun.net
    MAILGUN_ENDPOINT: str = "api.mailgun.net"
    MAILGUN_TIMEOUT_SECONDS: int = 15

    @property
    def mailgun_base_url(self) -> str:
        """Accept a bare host (`api.eu.mailgun.net`) as well as a full URL."""
        endpoint = self.MAILGUN_ENDPOINT.strip().rstrip("/")
        if endpoint.startswith(("http://", "https://")):
            return endpoint
        return f"https://{endpoint}"

    # -- SMTP
    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_STARTTLS: bool = True
    SMTP_TIMEOUT_SECONDS: int = 15
    EMAIL_FROM: str = "contact@byebyeboss.fr"
    EMAIL_FROM_NAME: str = "Bye Bye Boss"
    MAIL_QUEUE_INTERVAL_MINUTES: int = 1
    MAIL_QUEUE_BATCH_SIZE: int = 20
    MAIL_MAX_ATTEMPTS: int = 5

    # ---- AI (CV parsing) ---------------------------------------------------
    # Used by the `cv` module to structure raw CV text into a candidate
    # profile. Same OpenAI account/key as `matching` below, but its own
    # (cheaper) model: this is a fixed-schema extraction task ("read the CV,
    # fill in this JSON"), not the kind of judgment call matching makes, so
    # it doesn't need a flagship-tier model -- confirmed cost driver, see
    # 2026-09-22 cost review. No key configured -> upload endpoint returns a
    # clear error instead of silently failing.
    OPENAI_API_KEY: str | None = None
    CV_OPENAI_MODEL: str = "gpt-5-mini"
    OPENAI_TIMEOUT_SECONDS: int = 60
    # Same OPENAI_API_KEY, a separate cheap embedding model -- used by
    # core/embeddings.py to rank offers by semantic similarity (see
    # matching/shortlist.py) instead of literal keyword overlap. Embeddings
    # are fast (well under OPENAI_TIMEOUT_SECONDS in practice), but given a
    # dedicated setting rather than reusing it, since the two calls have
    # nothing else in common.
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_TIMEOUT_SECONDS: int = 30
    CV_MAX_UPLOAD_MB: int = 5
    # Absolute path outside the git checkout so uploaded files survive a
    # deploy (which replaces the checkout's tracked files). On the server,
    # set this in .env to something like /var/lib/byebyeboss/cv-uploads.
    CV_UPLOAD_DIR: str = "./uploads/cv"

    # ---- Job offers (matching) ---------------------------------------------
    # Ingested from official, legitimate sources only -- see the `offers`
    # module's docstring for why scraping LinkedIn/Indeed/Glassdoor/Welcome
    # to the Jungle is deliberately not an option here. Either provider's
    # credentials may be left unset; ingestion just skips an unconfigured
    # one (see OfferProvider.is_configured) rather than failing.
    FRANCE_TRAVAIL_CLIENT_ID: str | None = None
    FRANCE_TRAVAIL_CLIENT_SECRET: str | None = None
    ADZUNA_APP_ID: str | None = None
    ADZUNA_APP_KEY: str | None = None
    ADZUNA_COUNTRY: str = "fr"
    # Comma-separated search terms ingestion loops over for each configured
    # provider -- broad enough to cover the ESN-heavy profiles the platform
    # targets today; extend via .env as more profile types are supported.
    OFFERS_SEARCH_KEYWORDS: str = (
        "chef de projet,product owner,business analyst,scrum master,"
        "data analyst,développeur,consultant"
    )
    # Results requested per keyword per provider per run -- kept modest to
    # respect Adzuna's free-tier rate limits (25 calls/min, 250/day).
    OFFERS_MAX_PER_KEYWORD: int = 50
    OFFERS_INGESTION_INTERVAL_MINUTES: int = 60
    # How many offer embeddings are computed concurrently per ingestion run
    # (see EMBEDDING_MODEL above). Only new/changed/never-embedded offers are
    # embedded at all (see OffersIngestionService._upsert_batch), so this
    # only matters for a large batch -- e.g. the first run after this
    # feature was deployed, backfilling every existing offer at once.
    OFFERS_EMBEDDING_CONCURRENCY: int = 8

    @property
    def offers_search_keywords(self) -> list[str]:
        return [k.strip() for k in self.OFFERS_SEARCH_KEYWORDS.split(",") if k.strip()]

    # ---- Matching (CV <-> offers) ------------------------------------------
    # Uses the same OpenAI account/key as the `cv` module (OPENAI_API_KEY
    # above), but its own model setting: matching is a heavier judgment call
    # (weighing hard/medium/soft blockers, producing two internally
    # consistent scores) than the cv module's fixed-schema extraction, so it
    # keeps the flagship-tier model for now. Still the dominant cost driver
    # (2026-09-22 cost review: runs hourly for every candidate, up to
    # MATCHING_MAX_OFFERS_PER_CANDIDATE offers each) -- swapping this one too
    # is the next step, once a side-by-side quality check confirms a cheaper
    # model still produces trustworthy scores. Runs as a background job (see
    # app/modules/matching/jobs.py) rather than on-demand: a candidate's
    # dashboard reads pre-computed results instead of waiting on an LLM call.
    MATCHING_OPENAI_MODEL: str = "gpt-5"
    #
    # Its own timeout, separate from OPENAI_TIMEOUT_SECONDS: that one is tuned
    # for the cv module's fast "low" reasoning/verbosity extraction calls.
    # Matching uses "medium"/"medium" (see matching/gateway.py) for a heavier
    # judgment task, which routinely takes well over a minute -- reusing the
    # cv module's 60s timeout made every real call time out on all 3 retries
    # and fail every pair (observed in production: 2026-09-20).
    MATCHING_OPENAI_TIMEOUT_SECONDS: int = 180
    # How many offers are scored concurrently per candidate profile. Each LLM
    # call is network-bound (1-2 minutes via OpenAI) and touches no shared
    # state, so running several at once cuts a run's wall-clock time roughly
    # by this factor without changing total token cost. Keep modest to stay
    # within your OpenAI account's concurrent-request/rate limits.
    MATCHING_CONCURRENCY: int = 4
    # Was 60: an hourly re-scan of every complete profile is more than a
    # freshly-launched platform needs and was the single biggest lever on
    # LLM spend (2026-09-22 cost review) -- a new best-match offer showing up
    # up to 3h later instead of within the hour is a fair trade for a 3x cut
    # in how often the scoring job runs at all. Lower again once real usage
    # data (candidates per day, offers ingested per hour) justifies it.
    MATCHING_INTERVAL_MINUTES: int = 180
    # How many of the most relevant offers (see shortlist.py's keyword-overlap
    # heuristic) are actually scored by the LLM per candidate per run. Bounds
    # cost -- without this, cost would grow with the full offer pool size.
    # Was 15: halved (2026-09-22 cost review) -- also shrinks how often a
    # *new* offer bumps into the shortlist at all (the main source of fresh,
    # non-skippable LLM calls), on top of the direct per-run cap.
    MATCHING_MAX_OFFERS_PER_CANDIDATE: int = 8
    # Only offers ingested within this window are even considered for
    # shortlisting -- keeps the in-memory shortlisting step (and the pool of
    # "still relevant" offers) bounded as the offers table grows.
    MATCHING_MAX_OFFER_POOL_DAYS: int = 30

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
