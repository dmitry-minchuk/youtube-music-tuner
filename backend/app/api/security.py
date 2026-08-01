"""Host allowlist, session/CSRF and Origin guards (docs/08 section 1, docs/10 section 3).

* Unknown ``Host`` is rejected before routing — DNS rebinding protection.
* ``GET /api/v1/session`` issues a HttpOnly SameSite=Strict cookie plus a
  bound CSRF token; every mutation must present both and an exact Origin.
* The session lives in memory only and is invalidated by a restart.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.errors import ApiError, ForbiddenRequest, error_payload
from app.settings import Settings

SESSION_COOKIE = "tuner_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PUBLIC_PATHS = frozenset({"/health/live", "/health/ready"})


class SessionStore:
    """In-process session registry: session id -> CSRF token."""

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def create(self) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        self._tokens[session_id] = csrf_token
        return session_id, csrf_token

    def verify(self, session_id: str | None, csrf_token: str | None) -> bool:
        if not session_id or not csrf_token:
            return False
        expected = self._tokens.get(session_id)
        if expected is None:
            return False
        return hmac.compare_digest(expected, csrf_token)

    def clear(self) -> None:
        self._tokens.clear()


def _hostname_matches(header: str, allowed: frozenset[str]) -> bool:
    return header.strip().lower() in allowed


class GuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings, sessions: SessionStore) -> None:
        super().__init__(app)
        self._settings = settings
        self._sessions = sessions

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid.uuid4())

        host = request.headers.get("host", "")
        if not _hostname_matches(host, self._settings.allowed_hosts):
            return self._reject("Unknown Host header", request)

        if request.method not in SAFE_METHODS and request.url.path not in PUBLIC_PATHS:
            origin = request.headers.get("origin")
            if origin is not None and origin not in self._settings.allowed_origins:
                return self._reject("Origin not allowed", request)
            session_id = request.cookies.get(SESSION_COOKIE)
            csrf_token = request.headers.get(CSRF_HEADER)
            if not self._sessions.verify(session_id, csrf_token):
                return self._reject("Missing or invalid session token", request)

        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    @staticmethod
    def _reject(message: str, request: Request) -> JSONResponse:
        error = ForbiddenRequest(message)
        request_id = getattr(request.state, "request_id", "unknown")
        return JSONResponse(
            status_code=error.status_code,
            content=error_payload(error, request_id),
        )


def content_security_policy(settings: Settings) -> str:
    """Allow our own resources plus the YouTube IFrame player, no unsafe-eval."""
    origins = " ".join(sorted(settings.allowed_origins))
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self' https://www.youtube.com https://s.ytimg.com",
            "frame-src https://www.youtube.com https://www.youtube-nocookie.com",
            "img-src 'self' data: https://i.ytimg.com https://lh3.googleusercontent.com "
            "https://yt3.ggpht.com",
            "style-src 'self' 'unsafe-inline'",
            f"connect-src 'self' {origins}".strip(),
            "frame-ancestors 'self'",
            "base-uri 'self'",
            "form-action 'self'",
        ]
    )


class CspMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._policy = content_security_policy(settings)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", self._policy)
        return response


__all__ = [
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "ApiError",
    "CspMiddleware",
    "GuardMiddleware",
    "SessionStore",
    "content_security_policy",
]
