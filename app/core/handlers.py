"""Exception handlers producing a consistent JSON error envelope."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.core.monitoring_events import report_incident
from app.core.security import decode_token

logger = get_logger("app.errors")


def _envelope(code: str, message: str, details: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


def _json_safe_errors(errors: list[dict]) -> list[dict]:
    """Pydantic puts the raw exception in ``ctx`` for custom/email validators,
    which JSON cannot serialize: keep only plain values (the rest as text)."""
    safe = []
    for err in errors:
        err = dict(err)
        ctx = err.get("ctx")
        if ctx:
            err["ctx"] = {
                k: v if isinstance(v, str | int | float | bool | None) else str(v)
                for k, v in ctx.items()
            }
        safe.append(err)
    return safe


def _user_id_from_request(request: Request) -> uuid.UUID | None:
    """Best effort: who was logged in when the request failed."""
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    try:
        return uuid.UUID(decode_token(header[7:].strip())["sub"])
    except Exception:
        return None


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details or None),
            headers=exc.headers or None,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "validation_error",
                "Validation failed.",
                _json_safe_errors(exc.errors()),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope("http_error", str(exc.detail)),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error=str(exc))
        route = request.scope.get("route")
        path = getattr(route, "path", None) or request.url.path
        await report_incident(
            kind="server",
            fingerprint=f"server:{type(exc).__name__}:{request.method}:{path}",
            title=f"Erreur serveur sur {request.method} {path}",
            context=f"Serveur · {type(exc).__name__}",
            where=f"{request.method} {path}",
            technical_cause=f"{type(exc).__name__}: {exc}",
            user_message="Une erreur inattendue s'est produite.",
            user_id=_user_id_from_request(request),
        )
        return JSONResponse(
            status_code=500,
            content=_envelope("internal_error", "An unexpected error occurred."),
        )
