"""System, session and auth-status endpoints (docs/08 sections 1-2)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.security import CSRF_HEADER, SESSION_COOKIE
from app.integrations.youtube_music.auth import OAuthStatus, read_oauth_status
from app.persistence.database import get_session
from app.persistence.models import (
    ApiCallLedger,
    Job,
    ModelSnapshot,
    PlaybackSession,
    SyncRun,
)
from app.persistence.models import (
    utcnow as now_utc,
)
from app.settings import get_settings

router = APIRouter(prefix="/api/v1", tags=["system"])

APP_VERSION = "0.1.0"


@router.get("/session")
def create_session(request: Request, response: Response) -> dict[str, str]:
    """Issue the local session cookie and its bound CSRF token."""
    sessions = request.app.state.sessions
    session_id, csrf_token = sessions.create()
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        httponly=True,
        samesite="strict",
        secure=False,  # loopback HTTP only
        path="/",
    )
    return {"csrfToken": csrf_token, "headerName": CSRF_HEADER}


@router.get("/system/status")
def system_status(db: Session = Depends(get_session)) -> dict[str, Any]:
    settings = get_settings()
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    day_ago = now - dt.timedelta(days=1)

    last_sync = db.scalar(
        select(SyncRun.finished_at)
        .where(SyncRun.status == "SUCCESS")
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )
    last_train = db.scalar(
        select(ModelSnapshot.created_at).order_by(ModelSnapshot.created_at.desc()).limit(1)
    )
    pending_jobs = db.scalar(
        select(func.count()).select_from(Job).where(Job.status.in_(("PENDING", "RUNNING")))
    )
    calls_today = db.scalar(
        select(func.count()).select_from(ApiCallLedger).where(ApiCallLedger.started_at >= day_ago)
    )
    qualified_sessions = db.scalar(
        select(func.count()).select_from(PlaybackSession).where(PlaybackSession.qualified.is_(True))
    )

    oauth: OAuthStatus = read_oauth_status(settings)

    return {
        "appVersion": APP_VERSION,
        "serverTime": now.isoformat() + "Z",
        "publicPort": settings.public_port,
        "autoPublish": settings.auto_publish,
        "youtube": {"connected": oauth.connected, "reason": oauth.reason},
        "lastSuccessfulSyncAt": last_sync.isoformat() + "Z" if last_sync else None,
        "lastTrainingAt": last_train.isoformat() + "Z" if last_train else None,
        "pendingJobs": pending_jobs or 0,
        "externalCallsLast24h": calls_today or 0,
        "qualifiedSessions": qualified_sessions or 0,
    }


@router.get("/auth/status")
def auth_status() -> dict[str, Any]:
    """Connected account summary without any secret material."""
    status = read_oauth_status(get_settings())
    return {
        "connected": status.connected,
        "reason": status.reason,
        "clientConfigured": status.client_configured,
        "tokenPresent": status.token_present,
    }


@router.post("/auth/disconnect")
def auth_disconnect(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Cancel pending jobs and remove the local OAuth token.

    The Google-side grant is not revoked from here; the UI shows the account
    page link for that (docs/10 section 4).
    """
    settings = get_settings()
    cancelled = 0
    for job in db.scalars(select(Job).where(Job.status.in_(("PENDING", "RUNNING")))):
        job.status = "CANCELLED"
        job.finished_at = now_utc()
        cancelled += 1

    token_removed = False
    if settings.oauth_file.is_file():
        settings.oauth_file.unlink()
        token_removed = True

    # Disconnect means disconnect: browser cookies go too.
    cookies_removed = settings.browser_auth_file.is_file()
    settings.browser_auth_file.unlink(missing_ok=True)

    return {
        "tokenRemoved": token_removed,
        "cookiesRemoved": cookies_removed,
        "cancelledJobs": cancelled,
        "revokeUrl": "https://myaccount.google.com/permissions",
    }
