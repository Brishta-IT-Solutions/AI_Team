"""Error envelope (FRD FR-23): {code, message, retryable, correlation_id, field_errors}."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    status_code = 400
    code = "bad_request"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        field_errors: dict[str, str] | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.field_errors = field_errors or {}
        self.details = details or {}


class Unauthenticated(ApiError):
    status_code, code = 401, "unauthenticated"


class Forbidden(ApiError):
    status_code, code = 403, "forbidden"


class NotFound(ApiError):
    """Also used to conceal another project's objects (AT-15)."""

    status_code, code = 404, "not_found"


class Conflict(ApiError):
    status_code, code = 409, "conflict"


class Unprocessable(ApiError):
    status_code, code = 422, "unprocessable"


class Unavailable(ApiError):
    status_code, code, retryable = 503, "unavailable", True
