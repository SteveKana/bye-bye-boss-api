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

    # ---- Google Sign-In ---------------------------------------------------
    # The OAuth 2.0 "Web application" client id from Google Cloud Console
    # (Google Auth Platform > Clients). Public by nature -- it identifies
    # the app to Google, it isn't a secret -- but required: it's the
    # "audience" every Google ID token must have been issued for, so
    # without it POST /auth/google always refuses (see
    # google_oauth.verify_google_id_token). No client secret is needed:
    # the frontend uses Google Identity Services' ID-token flow, not a
    # server-side redirect exchange.
    GOOGLE_CLIENT_ID: str | None = None

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
    # (2026-09-22 cost review: runs once daily for every candidate, up to
    # MATCHING_MAX_OFFERS_PER_CANDIDATE offers each -- see app/modules/
    # matching/jobs.py for the schedule) -- swapping this one too is the
    # next step, once a side-by-side quality check (see
    # scripts/compare_matching_models.py) confirms a cheaper model still
    # produces trustworthy scores: an early 3-case run (2026-09-22) showed
    # real divergences on blocker detection and semantic-match recall, so
    # it's on hold pending a larger sample. Runs as a background job rather
    # than on-demand: a candidate's dashboard reads pre-computed results
    # instead of waiting on an LLM call.
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
    # How often the job itself runs lives in app/modules/matching/jobs.py
    # (a fixed daily cron, not a setting here) -- was an hourly interval,
    # then every 3h, then once a day at a fixed local time (2026-09-22 cost
    # review): the single biggest lever on LLM spend, since it bounds how
    # often *any* candidate gets rescanned at all, not just how many offers
    # each rescan touches (see MATCHING_MAX_OFFERS_PER_CANDIDATE below).
    # How many offers are sent to the (gpt-5) full analysis per candidate per
    # run, after the intermediate pre-filter below has kept only the ones
    # worth it. Was 15, then 8 (2026-09-22 cost review), now 5 (Steve,
    # 2026-10-04: 5 offers/day x 5 days = the 25-offer history shown on
    # /opportunites).
    MATCHING_MAX_OFFERS_PER_CANDIDATE: int = 5
    # Intermediate pre-filter (Steve, 2026-10-04): the offers closest to the
    # CV by embedding similarity (cheap, but blind to hard requirements --
    # it let ATS < 40 offers through) are first scored by a cheaper model on
    # the SAME ATS grid (see prompt.build_prefilter_prompt); only those with
    # an ATS score >= MATCHING_PREFILTER_MIN_ATS go on to the full analysis.
    MATCHING_PREFILTER_POOL_SIZE: int = 30
    MATCHING_PREFILTER_MODEL: str = "gpt-5-mini"
    MATCHING_PREFILTER_MIN_ATS: int = 75
    # A daily run only looks at offers ingested within this many days (an
    # offer already seen for a candidate -- whatever its outcome -- is never
    # pre-filtered or analysed twice); a brand-new profile's first run looks
    # at the whole MATCHING_MAX_OFFER_POOL_DAYS window instead.
    MATCHING_DAILY_POOL_DAYS: int = 2
    # OpenAI Batch API (50% cheaper, results within 24h): max requests per
    # submitted batch (OpenAI's own hard limit is 50,000), and how many times
    # a pair is retried after a failed/unparseable result before giving up.
    MATCHING_BATCH_MAX_REQUESTS: int = 5000
    MATCHING_MAX_ATTEMPTS: int = 3
    # /matching/top (the /opportunites history) returns this many most
    # recent offers; the dashboard shows DASHBOARD_TOP_COUNT of those that
    # are new to the candidate or were first shown on it TODAY (calendar day
    # in DASHBOARD_TIMEZONE) -- from the next day on they live in /opportunites.
    MATCHING_HISTORY_LIMIT: int = 25
    DASHBOARD_TOP_COUNT: int = 5
    DASHBOARD_TIMEZONE: str = "Europe/Paris"
    # Minimum final ATS score for a scored match to appear on the dashboard
    # (the /opportunites ATS filter defaults to the same value front-side).
    DASHBOARD_MIN_ATS: int = 75
    # Only offers ingested within this window are even considered for
    # shortlisting -- keeps the in-memory shortlisting step (and the pool of
    # "still relevant" offers) bounded as the offers table grows.
    MATCHING_MAX_OFFER_POOL_DAYS: int = 30
    # Freshness cap (Steve, 2026-10-04): an offer is only ever shortlisted
    # for a candidate -- first run included -- if it was PUBLISHED at most
    # this many days ago (ingestion date when the source gave no publish
    # date). Without it a brand-new profile's first run, which looks at the
    # whole MATCHING_MAX_OFFER_POOL_DAYS window, showed offers weeks old.
    MATCHING_MAX_OFFER_AGE_DAYS: int = 5

    # French labels for the skill names shown with an analysis (see
    # matching/skill_labels.py): the cheap model that translates each internal
    # concept once into the shared glossary, and how many matches one
    # background run catches up on.
    MATCHING_LABELS_MODEL: str = "gpt-5-mini"
    MATCHING_LABELS_TIMEOUT_SECONDS: int = 120
    MATCHING_LABELS_MATCHES_PER_RUN: int = 2000

    # ---- Regret Index (SimplyHired-sourced employee sentiment) ------------
    # Steve's call (2026-09-30), after confirming no legitimate structured
    # employee-review API exists at a cost/access level this project can use
    # (Glassdoor/Google: no usable public API, and getting one requires
    # defeating CAPTCHA/anti-bot measures -- refused outright, see
    # simplyhired_gateway.py's docstring; Indeed: partner API covers job
    # postings only, no reviews; ChooseMyCompany: real API exists but is
    # access-gated, contact-only). Originally built against Reddit (first its
    # OAuth2 API, then -- after Reddit locked down self-serve app creation --
    # its public search endpoint), kept in reddit_gateway.py/regret_gateway.py
    # for reference but no longer called: Steve moved to SimplyHired.fr's
    # public company-review pages instead, which publish an aggregate star
    # rating, category breakdowns and a review count with no CAPTCHA/login
    # wall observed, and need no LLM step since the data is already
    # structured (see simplyhired_gateway.py).
    SIMPLYHIRED_BASE_URL: str = "https://www.simplyhired.fr"
    # A descriptive-but-browser-like User-Agent -- SimplyHired serves HTML
    # for browsers, not a JSON API, so an httpx-default UA is more likely to
    # be treated as a bot than a real one; this identifies the client
    # honestly without doing anything to defeat detection.
    SIMPLYHIRED_USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
    SIMPLYHIRED_REQUEST_TIMEOUT_SECONDS: int = 20
    # A company's regret score is cached this long before being
    # recomputed -- employee sentiment doesn't shift day to day, and this
    # bounds scraping to roughly once per company per window rather than
    # once per candidate match. Also the effective cadence for the lazy,
    # on-demand path (MatchingService._upsert); the monthly bulk job in
    # regret_jobs.py is the primary refresh mechanism and forces a refresh
    # regardless of this TTL, so this mostly matters between two monthly runs
    # or for a company first encountered outside of one.
    REGRET_CACHE_TTL_DAYS: int = 30
    # Below this many SimplyHired reviews, the score stays "unavailable"
    # rather than treating a handful of reviews as representative.
    REGRET_MIN_REVIEWS: int = 5
    # Gates regret_jobs.py's monthly bulk refresh, same convention as
    # SCHEDULER_ENABLED itself -- lets this be switched off independently
    # (e.g. in a review-heavy dev/staging run) without touching the daily
    # matching sync.
    REGRET_MONTHLY_REFRESH_ENABLED: bool = True

    # ---- Regret Index, Indeed reviews via Bright Data (2026-10-02) --------
    # SimplyHired's own review endpoint caps at a fixed 10 reviews per
    # company no matter how it's queried (verified directly, see
    # simplyhired_gateway.py's docstring) -- nowhere near the much larger
    # count SimplyHired itself cites as coming from Indeed. Steve asked
    # ("un maximum") to also pull from Indeed directly; Indeed's own review
    # pages require an established browser session (a plain request gets
    # 403 -- verified), the same anti-bot gate this project already refuses
    # to defeat itself for Glassdoor/Google. Bright Data's Web Unlocker is a
    # paid third-party service that has already built that bypass and sells
    # access to it (https://brightdata.com/products/web-unlocker) -- this
    # project pays for that access rather than building the bypass itself.
    #
    # Steve has to create the Bright Data account and Web Unlocker zone
    # himself (this project never creates third-party accounts) and set
    # BRIGHTDATA_API_KEY / BRIGHTDATA_WEB_UNLOCKER_ZONE below -- everything
    # here stays inert (indeed_gateway.is_configured() -> False) until he
    # does, same pattern as FRANCE_TRAVAIL_CLIENT_ID/SECRET above.
    BRIGHTDATA_API_KEY: str = ""
    # The zone name Steve picks when creating the Web Unlocker zone in his
    # Bright Data dashboard -- "web_unlocker1" is only Bright Data's own
    # example value, not a default that will work out of the box.
    BRIGHTDATA_WEB_UNLOCKER_ZONE: str = "web_unlocker1"
    BRIGHTDATA_REQUEST_TIMEOUT_SECONDS: int = 30
    # Indeed shows 20 reviews per page (verified: .../reviews?start=20 is
    # the real "page 2" link) -- each Bright Data request unblocks one such
    # page, billed per request, not per review. Capped low by default (1
    # page = up to 20 reviews/company/month) to keep cost predictable while
    # Steve gets a feel for real usage against Bright Data's own dashboard;
    # raise it once he's seen real volume/cost there.
    INDEED_REVIEWS_MAX_PAGES_PER_COMPANY: int = 1

    # ---- Regret Index, Reddit-era settings (kept, unused) ------------------
    # reddit_gateway.py/regret_gateway.py/regret_prompt.py/regret_schema.py
    # still exist and still read these, but nothing calls them any more
    # (regret_service.py now goes through simplyhired_gateway.py above) --
    # kept only so that code still runs as-is if this is ever reverted,
    # rather than leaving it silently broken.
    REDDIT_USER_AGENT: str = "byebyeboss-regret-index/1.0"
    REGRET_OPENAI_MODEL: str = "gpt-5-mini"
    REGRET_OPENAI_TIMEOUT_SECONDS: int = 60
    REGRET_MIN_MENTIONS: int = 3

    # ---- CV optimization ("Adapter mon CV pour cette offre") --------------
    # A separate, on-demand LLM call (see matching/cv_optimization_gateway.py)
    # -- unlike the matching call above, this one is triggered by the
    # candidate clicking a button for one specific offer, not run in the
    # background for every candidate x offer pair, so it doesn't carry the
    # same "runs daily for everyone" cost multiplier. Kept as its own
    # setting anyway (not reusing MATCHING_OPENAI_MODEL) so it can be tuned
    # independently, same convention as the cv module's own CV_OPENAI_MODEL.
    #
    # gpt-5-mini rather than the flagship gpt-5 -- explicit call during the
    # current test phase (2026-09-22): no need to pay for the top-tier model
    # while the feature itself is still being validated. Revisit once real
    # usage confirms the cheaper model's rewrites/deductions stay reliable
    # enough (same quality-check step MATCHING_OPENAI_MODEL is still
    # pending before it gets the same treatment).
    CV_OPTIMIZATION_OPENAI_MODEL: str = "gpt-5-mini"
    # Same "medium" reasoning/verbosity as the matching call (heavier
    # judgment task than a fixed extraction schema) -- same generous timeout
    # for the same reason (see MATCHING_OPENAI_TIMEOUT_SECONDS above).
    CV_OPTIMIZATION_OPENAI_TIMEOUT_SECONDS: int = 180

    # ---- Notifications (daily brief) ---------------------------------------
    # "Le brief quotidien" -- best-matching offers, sent once a day per
    # candidate (see app/modules/notifications/jobs.py, scheduled right
    # after the matching job so it reads that day's fresh matches). Email
    # needs nothing here: it reuses the `mailer` module already configured
    # above. Discord needs nothing app-wide either -- each candidate pastes
    # their own webhook URL (see channels/discord_channel.py).
    NOTIFICATIONS_BRIEF_MAX_ITEMS: int = 5

    # -- WhatsApp (Meta Business Cloud API) -- the one channel that can't
    # go live from code alone: needs a verified Meta Business Account, a
    # registered phone number, and a message template pre-approved by Meta
    # (see channels/whatsapp_channel.py for why a template, not free text).
    # Left unset -> WhatsApp delivery is skipped with a log line, same
    # "unconfigured integration, don't crash the run" convention as the
    # offers module's providers.
    WHATSAPP_ACCESS_TOKEN: str | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    # Name of the pre-approved template in Meta Business Manager -- its 5
    # positional body variables must match channels/whatsapp_channel.py's
    # payload order (first name, job title, company name, match score,
    # offer link). One template message is sent per offer, not one
    # summarizing the whole brief -- see that module's docstring.
    WHATSAPP_TEMPLATE_NAME: str | None = None
    # True once WHATSAPP_TEMPLATE_NAME points at the template version that has
    # a 6th variable {{6}} = the "turn notifications off" link. Keep False
    # for the original 5-variable template (Meta rejects a variable-count
    # mismatch).
    WHATSAPP_TEMPLATE_HAS_SETTINGS_LINK: bool = False
    WHATSAPP_API_VERSION: str = "v21.0"
    WHATSAPP_TIMEOUT_SECONDS: int = 15

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
