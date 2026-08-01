"""Typed integration errors (docs/03 section 3).

The adapter never lets a raw ytmusicapi exception or payload escape into the
domain or the UI, and never logs a full external response.
"""

from __future__ import annotations


class IntegrationError(Exception):
    """Base class for every failure crossing the adapter boundary."""

    code = "YTM_ERROR"
    retryable = False


class AuthError(IntegrationError):
    code = "YTM_AUTH_REQUIRED"


class RateLimited(IntegrationError):
    code = "YTM_RATE_LIMITED"
    retryable = True

    def __init__(self, message: str = "", retry_after_seconds: int | None = None) -> None:
        super().__init__(message or self.code)
        self.retry_after_seconds = retry_after_seconds


class RemoteChanged(IntegrationError):
    code = "YTM_REMOTE_CHANGED"


class ParseError(IntegrationError):
    """The external payload no longer matches what the adapter can read."""

    code = "YTM_PARSE_ERROR"


class Unavailable(IntegrationError):
    code = "YTM_UNAVAILABLE"
    retryable = True
