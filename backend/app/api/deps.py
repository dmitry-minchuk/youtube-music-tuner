"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.integrations.youtube_music.adapter import YouTubeMusicAdapter
from app.integrations.youtube_music.ledger import SqlCallRecorder
from app.persistence.database import get_session
from app.settings import Settings, get_settings


def settings_dep() -> Settings:
    return get_settings()


def catalog_dep(
    request: Request,
    db: Session = Depends(get_session),
    settings: Settings = Depends(settings_dep),
) -> YouTubeMusicAdapter:
    """Adapter wired to the ledger for the current request.

    Tests override this dependency with a fake provider.
    """
    factory = getattr(request.app.state, "catalog_factory", None)
    recorder = SqlCallRecorder(db, request_id=getattr(request.state, "request_id", None))
    if factory is not None:
        return factory(recorder)
    return YouTubeMusicAdapter(settings, recorder=recorder)
