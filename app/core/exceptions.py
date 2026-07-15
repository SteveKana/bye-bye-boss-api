"""Domain exception hierarchy.

Raise these from services; `register_exception_handlers` turns them into a
uniform JSON error envelope:

    {"error": {"code": "not_found", "message": "...", "details": {...}}}

Anti-IDOR convention: when a resource exists but is not owned by the caller,
raise `NotFoundError` (404) — never leak its existence with a 403.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base application error. Subclass to define new error types."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}
        self.headers = headers or {}
        super().__init__(self.message)


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"
    message = "Bad request."


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "Authentication required."


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    message = "You do not have access to this resource."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "Resource already exists or is in conflict."


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "Validation failed."


class TooManyRequestsError(AppError):
    status_code = 429
    code = "rate_limit_exceeded"
    message = "Too many requests."
