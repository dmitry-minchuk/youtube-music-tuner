"""Library sync behaviour (docs/03 section 5, docs/11 section 4)."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select

from app.domain.catalog import RemoteHistoryItem, RemotePlaylist
from app.jobs.library_sync import SyncCooldownActive, check_cooldown, run_library_sync
from app.persistence.models import (
    LibraryTrackState,
    PlaybackSession,
    Track,
    utcnow,
)
from app.persistence.models import (
    RemoteHistoryItem as HistoryRow,
)
from app.persistence.models import (
    RemotePlaylist as PlaylistRow,
)
from tests.fakes import FakeCatalog, track


def test_sync_stores_likes_playlists_and_history(db_session) -> None:
    catalog = FakeCatalog(
        liked=[track("v1", "One"), track("v2", "Two", artist="Beta")],
        playlists=[RemotePlaylist(playlist_id="PL1", title="Morning", track_count=3)],
        history_items=[
            RemoteHistoryItem(track=track("v3", "Three"), play_order=1, period_label="Today")
        ],
    )

    run = run_library_sync(db_session, catalog)

    assert run.status == "SUCCESS"
    assert run.liked_count == 2
    assert run.playlist_count == 1
    assert run.history_count == 1
    assert db_session.get(Track, "v1") is not None
    assert db_session.get(LibraryTrackState, "v1").is_liked is True
    assert db_session.get(PlaylistRow, "PL1").title == "Morning"


def test_history_row_never_implies_a_skip(db_session) -> None:
    catalog = FakeCatalog(
        liked=[],
        history_items=[RemoteHistoryItem(track=track("v9", "Nine"), play_order=1)],
    )
    run_library_sync(db_session, catalog)

    row = db_session.scalar(select(HistoryRow))
    assert row.video_id == "v9"
    assert not hasattr(row, "played_seconds")
    # No playback session is fabricated from remote history.
    assert db_session.scalar(select(PlaybackSession)) is None


def test_unliking_remotely_clears_local_flag_but_keeps_the_track(db_session) -> None:
    catalog = FakeCatalog(liked=[track("v1", "One"), track("v2", "Two")])
    run_library_sync(db_session, catalog)

    catalog.liked = [track("v1", "One")]
    run_library_sync(db_session, catalog, force=True)

    assert db_session.get(LibraryTrackState, "v2").is_liked is False
    assert db_session.get(Track, "v2") is not None


def test_sync_upsert_does_not_erase_local_sessions(db_session) -> None:
    catalog = FakeCatalog(liked=[track("v1", "One")])
    run_library_sync(db_session, catalog)

    db_session.add(
        PlaybackSession(session_id="s1", video_id="v1", played_seconds=42.0, qualified=True)
    )
    db_session.flush()

    run_library_sync(db_session, catalog, force=True)

    session_row = db_session.get(PlaybackSession, "s1")
    assert session_row is not None
    assert session_row.played_seconds == 42.0


def test_vanished_playlist_is_flagged_not_deleted(db_session) -> None:
    catalog = FakeCatalog(
        liked=[],
        playlists=[
            RemotePlaylist(playlist_id="PL1", title="Morning"),
            RemotePlaylist(playlist_id="PL2", title="Focus"),
        ],
    )
    run_library_sync(db_session, catalog)

    catalog.playlists = [RemotePlaylist(playlist_id="PL1", title="Morning")]
    run_library_sync(db_session, catalog, force=True)

    gone = db_session.get(PlaylistRow, "PL2")
    assert gone is not None
    assert gone.remote_deleted_at is not None


def test_metadata_only_track_is_stored_but_not_playable(db_session) -> None:
    metadata_only = track("v-meta", "No Video")
    metadata_only = type(metadata_only)(
        video_id="v-meta",
        title="No Video",
        artists=metadata_only.artists,
        is_playable=False,
    )
    catalog = FakeCatalog(liked=[metadata_only])
    run_library_sync(db_session, catalog)

    assert db_session.get(Track, "v-meta").is_playable is False


def test_cooldown_blocks_a_second_sync_within_the_ttl(db_session) -> None:
    catalog = FakeCatalog(liked=[track("v1", "One")])
    run_library_sync(db_session, catalog)

    with pytest.raises(SyncCooldownActive):
        check_cooldown(db_session)

    # ...and is allowed again once the TTL has passed.
    check_cooldown(db_session, utcnow() + dt.timedelta(hours=7))


def test_a_missing_playlist_size_is_unknown_not_zero() -> None:
    """YouTube omits `count` for Liked Music and Episodes for Later; reporting
    those as 0 made the UI claim they were empty."""
    from app.integrations.youtube_music.parsers import parse_library_playlists

    parsed = parse_library_playlists(
        [
            {"playlistId": "LM", "title": "Liked Music", "description": None},
            {"playlistId": "PL1", "title": "Mine", "count": "12"},
            {"playlistId": "PL2", "title": "Empty", "count": "0"},
        ]
    )
    by_id = {playlist.playlist_id: playlist for playlist in parsed}
    assert by_id["LM"].track_count is None
    assert by_id["PL1"].track_count == 12
    assert by_id["PL2"].track_count == 0


def test_liked_music_reports_the_locally_known_size(authed_client, db_session) -> None:
    from app.persistence.models import LibraryTrackState, RemotePlaylist, Track

    db_session.add(RemotePlaylist(playlist_id="LM", title="Liked Music", track_count=None))
    for index in range(3):
        db_session.add(Track(video_id=f"liked-{index}", title=f"t{index}", is_playable=True))
        db_session.flush()
        db_session.add(LibraryTrackState(video_id=f"liked-{index}", is_liked=True))
    db_session.commit()

    body = authed_client.get("/api/v1/playlists").json()
    liked = next(p for p in body["remotePlaylists"] if p["playlistId"] == "LM")
    assert liked["trackCount"] == 3
