"""Snapshot-oriented library synchronisation (docs/03 section 5)."""

from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.youtube_music.errors import IntegrationError
from app.integrations.youtube_music.ledger import BudgetExceeded, CallBudget
from app.integrations.youtube_music.port import MusicCatalogPort
from app.persistence import repositories as repo
from app.persistence.models import SyncRun, utcnow

logger = logging.getLogger(__name__)

SYNC_TTL = dt.timedelta(hours=6)


class SyncCooldownActive(Exception):
    def __init__(self, next_allowed_at: dt.datetime) -> None:
        super().__init__("library sync is still within its TTL")
        self.next_allowed_at = next_allowed_at


def last_successful_sync(session: Session) -> SyncRun | None:
    return session.scalar(
        select(SyncRun)
        .where(SyncRun.status == "SUCCESS")
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )


def check_cooldown(session: Session, now: dt.datetime | None = None) -> None:
    now = now or utcnow()
    last = last_successful_sync(session)
    if last is not None and last.finished_at is not None:
        next_allowed = last.finished_at + SYNC_TTL
        if next_allowed > now:
            raise SyncCooldownActive(next_allowed)


def run_library_sync(
    session: Session,
    catalog: MusicCatalogPort,
    *,
    force: bool = False,
    include_history: bool = True,
) -> SyncRun:
    """Read likes, playlists and available history into the local cache."""
    now = utcnow()
    if not force:
        check_cooldown(session, now)
        CallBudget(session, now).check_library_sync()

    run = SyncRun(sync_run_id=str(uuid.uuid4()), status="RUNNING", started_at=now)
    session.add(run)
    session.flush()

    try:
        liked = catalog.liked_tracks()
        playlists = catalog.library_playlists()

        liked_ids: set[str] = set()
        for track in liked:
            repo.upsert_track(session, track)
            if track.video_id is not None:
                liked_ids.add(track.video_id)
        repo.mark_liked(session, liked_ids, run.sync_run_id)

        seen_playlists: set[str] = set()
        for playlist in playlists:
            repo.upsert_playlist(session, playlist)
            seen_playlists.add(playlist.playlist_id)
        repo.mark_missing_playlists(session, seen_playlists)

        history_count = 0
        if include_history:
            history = catalog.history()
            history_count = repo.record_history(session, history, now)

        run.status = "SUCCESS"
        run.liked_count = len(liked_ids)
        run.playlist_count = len(seen_playlists)
        run.history_count = history_count
    except (IntegrationError, BudgetExceeded) as exc:
        run.status = "FAILED"
        run.error_code = getattr(exc, "code", type(exc).__name__)
        logger.warning(
            "library sync failed",
            extra={"operation": "library_sync", "outcome": "FAILED", "error_code": run.error_code},
        )
        run.finished_at = utcnow()
        session.flush()
        raise
    finally:
        if run.finished_at is None:
            run.finished_at = utcnow()
        session.flush()

    logger.info(
        "library sync finished",
        extra={
            "operation": "library_sync",
            "outcome": "SUCCESS",
            "liked": run.liked_count,
            "playlists": run.playlist_count,
            "history": run.history_count,
        },
    )
    return run
