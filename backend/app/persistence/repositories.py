"""Repository helpers over the SQLAlchemy models.

Upserts never delete local telemetry: a track that disappeared remotely gets
``remote_deleted_at`` instead of being removed (docs/03 section 5).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog import RemotePlaylist as DomainPlaylist
from app.domain.catalog import RemotePlaylistSnapshot
from app.domain.catalog import Track as DomainTrack
from app.persistence.models import (
    Artist,
    LibraryTrackState,
    RemoteHistoryItem,
    RemotePlaylist,
    RemotePlaylistItem,
    Track,
    TrackArtist,
    utcnow,
)


def content_hash(video_ids: list[str] | tuple[str, ...]) -> str:
    payload = json.dumps(list(video_ids), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def upsert_track(session: Session, track: DomainTrack) -> Track | None:
    """Insert or refresh one track. Metadata-only entries are still stored."""
    if track.video_id is None:
        return None

    row = session.get(Track, track.video_id)
    if row is None:
        row = Track(video_id=track.video_id, title=track.title)
        session.add(row)

    row.title = track.title
    # Never invent a duration or an album we did not receive.
    if track.duration_seconds is not None:
        row.metadata_duration_seconds = track.duration_seconds
    if track.album_id is not None:
        row.album_id = track.album_id
    if track.album_title is not None:
        row.album_title = track.album_title
    if track.thumbnail_url is not None:
        row.thumbnail_url = track.thumbnail_url
    row.is_playable = track.is_playable
    row.remote_deleted_at = None
    row.updated_at = utcnow()

    _sync_artists(session, row, track)
    return row


def _sync_artists(session: Session, row: Track, track: DomainTrack) -> None:
    if not track.artists:
        return
    existing = {
        link.artist_id: link
        for link in session.scalars(select(TrackArtist).where(TrackArtist.track_id == row.video_id))
    }
    for ordinal, artist in enumerate(track.artists):
        if session.get(Artist, artist.artist_id) is None:
            session.add(Artist(artist_id=artist.artist_id, name=artist.name))
        link = existing.get(artist.artist_id)
        if link is None:
            session.add(
                TrackArtist(track_id=row.video_id, artist_id=artist.artist_id, ordinal=ordinal)
            )
        else:
            link.ordinal = ordinal


def library_state(session: Session, video_id: str) -> LibraryTrackState:
    row = session.get(LibraryTrackState, video_id)
    if row is None:
        row = LibraryTrackState(video_id=video_id)
        session.add(row)
        session.flush()
    return row


def mark_liked(session: Session, video_ids: set[str], snapshot_id: str) -> None:
    """Reconcile the liked set: newly liked in, unliked out, telemetry kept."""
    for video_id in video_ids:
        state = library_state(session, video_id)
        state.is_liked = True
        state.is_disliked = False
        state.is_in_library = True
        state.source_snapshot_id = snapshot_id
        state.updated_at = utcnow()

    previously_liked = session.scalars(
        select(LibraryTrackState).where(LibraryTrackState.is_liked.is_(True))
    ).all()
    for state in previously_liked:
        if state.video_id not in video_ids:
            state.is_liked = False
            state.updated_at = utcnow()


def upsert_playlist(session: Session, playlist: DomainPlaylist) -> RemotePlaylist:
    row = session.get(RemotePlaylist, playlist.playlist_id)
    if row is None:
        row = RemotePlaylist(playlist_id=playlist.playlist_id, title=playlist.title)
        session.add(row)
    row.title = playlist.title
    row.description = playlist.description
    row.track_count = playlist.track_count
    row.fetched_at = utcnow()
    row.remote_deleted_at = None
    return row


def store_playlist_snapshot(session: Session, snapshot: RemotePlaylistSnapshot) -> RemotePlaylist:
    row = session.get(RemotePlaylist, snapshot.playlist_id)
    if row is None:
        row = RemotePlaylist(playlist_id=snapshot.playlist_id, title=snapshot.title)
        session.add(row)
    row.title = snapshot.title
    row.description = snapshot.description
    row.track_count = len(snapshot.items)
    row.content_hash = content_hash(snapshot.video_ids)
    row.fetched_at = snapshot.fetched_at
    row.remote_deleted_at = None

    session.query(RemotePlaylistItem).filter(
        RemotePlaylistItem.playlist_id == snapshot.playlist_id
    ).delete(synchronize_session=False)

    for item in snapshot.items:
        if item.track.video_id is None:
            continue
        upsert_track(session, item.track)
        session.add(
            RemotePlaylistItem(
                playlist_id=snapshot.playlist_id,
                position=item.position,
                video_id=item.track.video_id,
                set_video_id=item.set_video_id,
            )
        )
    return row


def mark_missing_playlists(session: Session, seen_ids: set[str]) -> int:
    """Flag playlists that vanished remotely instead of deleting rows."""
    missing = 0
    for row in session.scalars(
        select(RemotePlaylist).where(RemotePlaylist.remote_deleted_at.is_(None))
    ):
        if row.playlist_id not in seen_ids:
            row.remote_deleted_at = utcnow()
            missing += 1
    return missing


def record_history(session: Session, items: list, observed_at: dt.datetime) -> int:
    """Store the bare fact of a remote play; never a duration or a skip."""
    stored = 0
    for item in items:
        video_id = item.track.video_id
        if video_id is None:
            continue
        upsert_track(session, item.track)
        exists = session.scalar(
            select(RemoteHistoryItem).where(
                RemoteHistoryItem.video_id == video_id,
                RemoteHistoryItem.observed_at == observed_at,
                RemoteHistoryItem.play_order == item.play_order,
            )
        )
        if exists is not None:
            continue
        session.add(
            RemoteHistoryItem(
                video_id=video_id,
                observed_at=observed_at,
                play_order=item.play_order,
                period_label=item.period_label,
            )
        )
        stored += 1
    return stored
