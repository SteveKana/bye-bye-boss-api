"""Application factory + lifespan wiring.

Startup order:
  1. configure logging
  2. discover modules (imports models, listeners, scheduled jobs)
  3. (dev) create tables
  4. run module startup hooks
  5. start scheduler

Routers are auto-registered from discovered modules under `/api/<version>`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.database import create_all, dispose, ping
from app.core.handlers import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import register_middlewares
from app.core.module import Module, discover_modules
from app.core.scheduler import start_scheduler, stop_scheduler

settings = get_settings()
configure_logging(level=settings.LOG_LEVEL, json_logs=settings.LOG_JSON)
logger = get_logger("app")

# Discover once at import time so Alembic / tests can reuse the same list.
MODULES: list[Module] = discover_modules()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await ping()
    if settings.DATABASE_AUTO_CREATE:
        await create_all()
    for module in MODULES:
        if module.on_startup:
            logger.info("module_startup", module=module.name)
            await module.on_startup()
    start_scheduler()
    logger.info("app_started", env=settings.APP_ENV)
    yield
    stop_scheduler()
    for module in reversed(MODULES):
        if module.on_shutdown:
            await module.on_shutdown()
    await dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.API_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url=f"{settings.api_prefix}/docs",
        redoc_url=f"{settings.api_prefix}/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
    )

    register_exception_handlers(app)
    register_middlewares(app)

    for module in MODULES:
        if module.router is not None:
            app.include_router(module.router, prefix=settings.api_prefix)

    @app.get("/health", tags=["system"])
    async def health() -> dict:
        return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}

    return app


app = create_app()
