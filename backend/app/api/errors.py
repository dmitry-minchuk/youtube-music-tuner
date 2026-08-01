"""Typed API errors and the single error response shape (docs/08 sections 1, 8).

No error response may contain OAuth bodies, headers or stack traces.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """Base error mapped to the documented ``{"error": {...}}`` envelope."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    retryable: bool = False

    def __init__(self, message: str = "", **details: object) -> None:
        super().__init__(message or self.code)
        self.message = message or self.code
        self.details = details


class ValidationFailed(ApiError):
    status_code = 400
    code = "VALIDATION_FAILED"


class YtmAuthRequired(ApiError):
    status_code = 401
    code = "YTM_AUTH_REQUIRED"


class PlaylistNotManaged(ApiError):
    status_code = 403
    code = "PLAYLIST_NOT_MANAGED"


class PlaylistUnverified(ApiError):
    status_code = 409
    code = "PLAYLIST_UNVERIFIED"


class RemoteChanged(ApiError):
    status_code = 409
    code = "REMOTE_CHANGED"


class JobAlreadyRunning(ApiError):
    status_code = 409
    code = "JOB_ALREADY_RUNNING"


class PlaylistQualityFailed(ApiError):
    status_code = 422
    code = "PLAYLIST_QUALITY_FAILED"


class LocalBudgetExceeded(ApiError):
    status_code = 429
    code = "LOCAL_BUDGET_EXCEEDED"
    retryable = True


class YtmParseError(ApiError):
    status_code = 502
    code = "YTM_PARSE_ERROR"


class YtmUnavailable(ApiError):
    status_code = 503
    code = "YTM_UNAVAILABLE"
    retryable = True


class CircuitOpen(ApiError):
    status_code = 503
    code = "CIRCUIT_OPEN"
    retryable = True


class ForbiddenRequest(ApiError):
    """Origin/CSRF/Host guard rejection."""

    status_code = 403
    code = "FORBIDDEN_REQUEST"


def error_payload(error: ApiError, request_id: str) -> dict[str, object]:
    body: dict[str, object] = {
        "code": error.code,
        "message": error.message,
        "requestId": request_id,
        "retryable": error.retryable,
    }
    body.update(error.details)
    return {"error": body}


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(exc, request_id),
    )


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Map FastAPI's 422 onto the documented 400 VALIDATION_FAILED."""
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=400,
        content=error_payload(ValidationFailed("request body is not valid"), request_id),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Never leak internals: the message is generic, details go to the log."""
    request_id = getattr(request.state, "request_id", "unknown")
    return JSONResponse(
        status_code=500,
        content=error_payload(ApiError("Internal error"), request_id),
    )
