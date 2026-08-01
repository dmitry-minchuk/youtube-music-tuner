"""Connecting YouTube Music from the UI (docs/03 section 2).

The original rule was that no credential ever passes through the web UI.
That was written assuming OAuth would work; it does not, and the working
path needs browser headers, so hand-copying them into a terminal was the
only way to connect. This endpoint accepts that paste over loopback
instead, behind the same guards as every other mutation (exact Origin,
SameSite=Strict session cookie, CSRF token, Host allowlist).

What has not changed: the value is never echoed back, never logged, and is
stored only as a 0600 file inside the container.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import catalog_dep
from app.api.library import map_integration_error
from app.integrations.youtube_music.auth import (
    SECRET_FILE_MODE,
    complete_browser_headers,
    ensure_secrets_dir,
    parse_browser_headers,
    read_oauth_status,
)
from app.integrations.youtube_music.errors import IntegrationError
from app.integrations.youtube_music.port import MusicCatalogPort
from app.persistence.database import get_session
from app.settings import Settings, get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

MAX_PASTE_BYTES = 32 * 1024


class BrowserHeadersRequest(BaseModel):
    headers: str = Field(min_length=1, max_length=MAX_PASTE_BYTES)


@router.post("/browser-headers")
def import_browser_headers(
    body: BrowserHeadersRequest,
    db: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
    catalog: MusicCatalogPort = Depends(catalog_dep),
) -> dict[str, Any]:
    """Validate a header paste, store it, and prove it works."""
    try:
        headers = complete_browser_headers(parse_browser_headers(body.headers))
    except ValueError as exc:
        raise errors.ValidationFailed(str(exc)) from exc

    ensure_secrets_dir(settings)
    from ytmusicapi import setup as ytmusic_setup

    normalized = "\n".join(f"{name}: {value}" for name, value in sorted(headers.items()))
    ytmusic_setup(filepath=str(settings.browser_auth_file), headers_raw=normalized)
    settings.browser_auth_file.chmod(SECRET_FILE_MODE)

    # Report success only after YouTube Music actually answers.
    try:
        liked = catalog.liked_tracks(limit=1)
    except IntegrationError as exc:
        settings.browser_auth_file.unlink(missing_ok=True)
        logger.warning(
            "browser headers rejected by YouTube Music",
            extra={"operation": "connect", "outcome": "FAILED", "error_code": exc.code},
        )
        raise map_integration_error(exc) from exc

    logger.info("connected via browser headers", extra={"operation": "connect"})
    status = read_oauth_status(settings)
    return {
        "connected": status.connected,
        "method": status.method,
        "likedTracksVisible": len(liked),
    }


@router.post("/forget-browser-headers")
def forget_browser_headers(settings: Settings = Depends(get_settings)) -> dict[str, Any]:
    """Drop stored cookies without touching telemetry or the library cache."""
    removed = settings.browser_auth_file.is_file()
    settings.browser_auth_file.unlink(missing_ok=True)
    status = read_oauth_status(settings)
    return {"removed": removed, "connected": status.connected, "method": status.method}
