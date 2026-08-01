"""Library, playlists, search and rating endpoints (docs/08 section 3)."""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import catalog_dep
from app.domain.catalog import Rating
from app.integrations.youtube_music.errors import (
    AuthError,
    IntegrationError,
    ParseError,
    RateLimited,
    Unavailable,
)
from app.integrations.youtube_music.ledger import BudgetExceeded, CallBudget
from app.integrations.youtube_music.port import MusicCatalogPort
from app.jobs import queue
from app.jobs.library_sync import SYNC_TTL, SyncCooldownActive, check_cooldown, last_successful_sync
from app.persistence import repositories as repo
from app.persistence.database import get_session
from app.persistence.models import (
    Artist,
    LibraryTrackState,
    ManagedPlaylist,
    RemotePlaylist,
    RemotePlaylistItem,
    Track,
    TrackArtist,
    utcnow,
)

router = APIRouter(prefix="/api/v1", tags=["library"])

DEFAULT_PAGE = 50
MAX_PAGE = 200
SEARCH_CACHE_TTL = dt.timedelta(hours=24)


def map_integration_error(exc: IntegrationError) -> errors.ApiError:
    if isinstance(exc, AuthError):
        return errors.YtmAuthRequired("YouTube Music authentication is required")
    if isinstance(exc, RateLimited):
        return errors.LocalBudgetExceeded("YouTube Music rate limited the request")
    if isinstance(exc, ParseError):
        return errors.YtmParseError("YouTube Music returned an incompatible payload")
    if isinstance(exc, Unavailable):
        return errors.YtmUnavailable("YouTube Music is temporarily unavailable")
    return errors.YtmUnavailable("YouTube Music call failed")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        offset = int(payload["o"])
    except (ValueError, KeyError, TypeError) as exc:
        raise errors.ValidationFailed("cursor is not valid") from exc
    return max(0, offset)


def _artists_for(db: Session, video_ids: list[str]) -> dict[str, list[str]]:
    if not video_ids:
        return {}
    rows = db.execute(
        select(TrackArtist.track_id, Artist.name)
        .join(Artist, Artist.artist_id == TrackArtist.artist_id)
        .where(TrackArtist.track_id.in_(video_ids))
        .order_by(TrackArtist.track_id, TrackArtist.ordinal)
    ).all()
    grouped: dict[str, list[str]] = {}
    for track_id, name in rows:
        grouped.setdefault(track_id, []).append(name)
    return grouped


def serialize_track(track: Track, artists: list[str]) -> dict[str, Any]:
    return {
        "videoId": track.video_id,
        "title": track.title,
        "artists": artists,
        "albumTitle": track.album_title,
        "durationSeconds": track.metadata_duration_seconds,
        "thumbnailUrl": track.thumbnail_url,
        "isPlayable": track.is_playable,
    }


@router.get("/library/tracks")
def library_tracks(
    view: Literal["liked", "recent", "discovered", "blocked", "all"] = "liked",
    cursor: str | None = None,
    limit: int = Query(DEFAULT_PAGE, ge=1, le=MAX_PAGE),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Local catalogue only — opening a screen never calls YouTube."""
    offset = _decode_cursor(cursor)
    query = select(Track).join(
        LibraryTrackState, LibraryTrackState.video_id == Track.video_id, isouter=True
    )

    if view == "liked":
        query = query.where(LibraryTrackState.is_liked.is_(True))
    elif view == "blocked":
        query = query.where(LibraryTrackState.is_disliked.is_(True))
    elif view == "discovered":
        query = query.where(
            (LibraryTrackState.is_liked.is_(False)) | (LibraryTrackState.video_id.is_(None))
        )

    query = query.where(Track.remote_deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Track.title).offset(offset).limit(limit + 1)).all()

    has_more = len(rows) > limit
    page = list(rows[:limit])
    artists = _artists_for(db, [row.video_id for row in page])

    return {
        "items": [serialize_track(row, artists.get(row.video_id, [])) for row in page],
        "total": total,
        "nextCursor": _encode_cursor(offset + limit) if has_more else None,
    }


@router.get("/playlists")
def list_playlists(db: Session = Depends(get_session)) -> dict[str, Any]:
    managed = {
        row.playlist_id: row
        for row in db.scalars(select(ManagedPlaylist))
        if row.playlist_id is not None
    }

    remote = []
    for row in db.scalars(select(RemotePlaylist).where(RemotePlaylist.remote_deleted_at.is_(None))):
        if row.playlist_id in managed:
            continue
        remote.append(
            {
                "playlistId": row.playlist_id,
                "title": row.title,
                "trackCount": row.track_count,
                "fetchedAt": row.fetched_at.isoformat() + "Z",
            }
        )

    tuner = [
        {
            "managedPlaylistId": row.managed_playlist_id,
            "playlistId": row.playlist_id,
            "kind": row.kind,
            "status": row.status,
            "temperature": row.temperature,
            "configuredTargetSize": row.configured_target_size,
            "lastPublishedAt": (
                row.last_published_at.isoformat() + "Z" if row.last_published_at else None
            ),
            "autoPublishEnabled": row.auto_publish_enabled,
        }
        for row in db.scalars(select(ManagedPlaylist))
    ]

    return {"tunerPlaylists": tuner, "remotePlaylists": remote}


@router.get("/playlists/{playlist_id}")
def playlist_detail(playlist_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    row = db.get(RemotePlaylist, playlist_id)
    if row is None:
        raise errors.ValidationFailed("playlist is not in the local cache")

    items = db.scalars(
        select(RemotePlaylistItem)
        .where(RemotePlaylistItem.playlist_id == playlist_id)
        .order_by(RemotePlaylistItem.position)
    ).all()
    tracks = {
        track.video_id: track
        for track in db.scalars(
            select(Track).where(Track.video_id.in_([item.video_id for item in items]))
        )
    }
    artists = _artists_for(db, [item.video_id for item in items])

    return {
        "playlistId": row.playlist_id,
        "title": row.title,
        "description": row.description,
        "trackCount": row.track_count,
        "contentHash": row.content_hash,
        "fetchedAt": row.fetched_at.isoformat() + "Z",
        "items": [
            {
                "position": item.position,
                "track": serialize_track(track, artists.get(item.video_id, []))
                if (track := tracks.get(item.video_id))
                else {"videoId": item.video_id},
            }
            for item in items
        ],
    }


@router.post("/sync")
def request_sync(db: Session = Depends(get_session)) -> dict[str, Any]:
    """Queue a library sync, respecting the TTL and the daily budget."""
    now = utcnow()
    try:
        check_cooldown(db, now)
    except SyncCooldownActive as exc:
        raise errors.LocalBudgetExceeded(
            "library sync is still within its 6 hour TTL",
            nextAllowedAt=exc.next_allowed_at.isoformat() + "Z",
        ) from exc

    budget = CallBudget(db, now)
    circuit = budget.circuit_state()
    if circuit.open:
        raise errors.CircuitOpen(f"external calls are paused ({circuit.reason})")
    try:
        budget.check_library_sync()
    except BudgetExceeded as exc:
        raise errors.LocalBudgetExceeded(str(exc), budget=exc.budget, limit=exc.limit) from exc

    try:
        job = queue.enqueue(db, queue.JOB_LIBRARY_SYNC, "library_sync")
    except queue.JobAlreadyActive as exc:
        raise errors.JobAlreadyRunning("a library sync is already queued") from exc

    return {"jobId": job.job_id, "status": job.status}


@router.get("/sync/status")
def sync_status(db: Session = Depends(get_session)) -> dict[str, Any]:
    last = last_successful_sync(db)
    active = queue.find_active(db, "library_sync")
    return {
        "lastSuccessfulSyncAt": (
            last.finished_at.isoformat() + "Z" if last and last.finished_at else None
        ),
        "nextAllowedAt": (
            (last.finished_at + SYNC_TTL).isoformat() + "Z" if last and last.finished_at else None
        ),
        "activeJob": {"jobId": active.job_id, "status": active.status} if active else None,
        "likedCount": last.liked_count if last else 0,
        "playlistCount": last.playlist_count if last else 0,
    }


@router.get("/search")
def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_session),
    catalog: MusicCatalogPort = Depends(catalog_dep),
) -> dict[str, Any]:
    """Explicit user action only; results are cached into the local catalogue."""
    budget = CallBudget(db)
    circuit = budget.circuit_state()
    if circuit.open:
        raise errors.CircuitOpen(f"external calls are paused ({circuit.reason})")

    try:
        results = catalog.search(q, limit=limit)
    except IntegrationError as exc:
        raise map_integration_error(exc) from exc

    for track in results:
        repo.upsert_track(db, track)

    return {
        "query": q,
        "items": [
            {
                "videoId": track.video_id,
                "title": track.title,
                "artists": [artist.name for artist in track.artists],
                "albumTitle": track.album_title,
                "durationSeconds": track.duration_seconds,
                "thumbnailUrl": track.thumbnail_url,
            }
            for track in results
        ],
    }


class RatingRequest(BaseModel):
    desiredState: Literal["LIKE", "DISLIKE", "INDIFFERENT"] = Field()


@router.put("/tracks/{video_id}/rating")
def set_rating(
    video_id: str,
    body: RatingRequest,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Record the explicit rating locally, then queue one mutable sync command.

    The dedupe key never contains the state, so rapid toggles collapse into a
    single command whose payload carries the newest revision (docs/03 s.6).
    """
    if db.get(Track, video_id) is None:
        raise errors.ValidationFailed("unknown track")

    desired = Rating(body.desiredState)
    state = repo.library_state(db, video_id)
    state.desired_rating = desired.value
    state.rating_revision += 1
    state.rating_sync_status = "PENDING"
    state.is_liked = desired is Rating.LIKE
    state.is_disliked = desired is Rating.DISLIKE
    state.updated_at = utcnow()
    db.flush()

    queue.enqueue(
        db,
        queue.JOB_RATING_SYNC,
        f"rating:{video_id}",
        {"videoId": video_id, "desiredState": desired.value, "revision": state.rating_revision},
        not_before=utcnow() + dt.timedelta(seconds=2),
        replace_payload=True,
    )

    return {
        "videoId": video_id,
        "desiredState": desired.value,
        "revision": state.rating_revision,
        "syncStatus": state.rating_sync_status,
    }
