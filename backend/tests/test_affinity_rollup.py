"""Affinity aggregates (docs/07 section 4).

These rows are what turns listening history into ranking signal. While they
were empty every history-derived feature silently evaluated to zero.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session

from app.persistence.models import (
    Artist,
    ArtistAffinity,
    PlaybackSession,
    Track,
    TrackAffinity,
    TrackArtist,
    utcnow,
)
from app.recommender.affinity import (
    REWARD_HALF_LIFE_DAYS,
    rebuild_all,
    recompute_track_affinity,
    refresh_for_track,
)


def add_track(db: Session, video_id: str, artist: str = "Alpha") -> None:
    db.add(Track(video_id=video_id, title=video_id, is_playable=True))
    artist_id = f"UC{artist}"
    if db.get(Artist, artist_id) is None:
        db.add(Artist(artist_id=artist_id, name=artist))
    db.add(TrackArtist(track_id=video_id, artist_id=artist_id, ordinal=0))
    db.flush()


def add_session(
    db: Session,
    session_id: str,
    video_id: str,
    *,
    reward: float | None = 0.5,
    completed: bool = False,
    early_skip: bool = False,
    started_at: dt.datetime | None = None,
    explicit_rating: str | None = None,
) -> None:
    db.add(
        PlaybackSession(
            session_id=session_id,
            video_id=video_id,
            started_at=started_at or utcnow(),
            qualified=True,
            reward=reward,
            completed=completed,
            early_skip=early_skip,
            explicit_rating=explicit_rating,
        )
    )
    db.flush()


def test_sessions_fold_into_counters(db_session) -> None:
    add_track(db_session, "t1")
    add_session(db_session, "s1", "t1", completed=True, reward=0.8)
    add_session(db_session, "s2", "t1", early_skip=True, reward=-0.5)

    row = recompute_track_affinity(db_session, "t1")

    assert row.plays_all == 2
    assert row.plays_7d == 2
    assert row.completions == 1
    assert row.skips == 1
    assert row.last_skipped_at is not None
    assert -1.0 <= row.decayed_reward <= 1.0


def test_recent_opinions_outweigh_old_ones(db_session) -> None:
    add_track(db_session, "t1")
    add_session(
        db_session,
        "old",
        "t1",
        reward=1.0,
        started_at=utcnow() - dt.timedelta(days=REWARD_HALF_LIFE_DAYS * 4),
    )
    add_session(db_session, "new", "t1", reward=-1.0)

    row = recompute_track_affinity(db_session, "t1")
    assert row.decayed_reward < -0.5


def test_rollup_is_idempotent(db_session) -> None:
    """Telemetry batches get replayed; counters must not inflate."""
    add_track(db_session, "t1")
    add_session(db_session, "s1", "t1", completed=True)

    first = recompute_track_affinity(db_session, "t1").plays_all
    for _ in range(3):
        recompute_track_affinity(db_session, "t1")
    assert db_session.get(TrackAffinity, "t1").plays_all == first == 1


def test_artist_affinity_covers_every_credited_artist(db_session) -> None:
    add_track(db_session, "t1", artist="Alpha")
    db_session.add(Artist(artist_id="UCBeta", name="Beta"))
    db_session.add(TrackArtist(track_id="t1", artist_id="UCBeta", ordinal=1))
    db_session.flush()
    add_session(db_session, "s1", "t1", reward=0.9, completed=True)

    refresh_for_track(db_session, "t1")

    for artist_id in ("UCAlpha", "UCBeta"):
        row = db_session.get(ArtistAffinity, artist_id)
        assert row is not None
        assert row.plays_all == 1
        assert row.decayed_reward > 0


def test_rebuild_all_covers_every_played_track(db_session) -> None:
    for index in range(3):
        add_track(db_session, f"t{index}", artist=f"A{index}")
        add_session(db_session, f"s{index}", f"t{index}")

    tracks, artists = rebuild_all(db_session)
    assert tracks == 3
    assert artists == 3
    assert db_session.query(TrackAffinity).count() == 3


def test_telemetry_ingest_updates_affinity(authed_client, db_session) -> None:
    """The aggregate must move on the current track, not on tomorrow's job."""
    add_track(db_session, "vid-1")
    db_session.commit()

    payload = {
        "schemaVersion": 1,
        "events": [
            {
                "clientEventId": "e1",
                "sessionId": "sess-1",
                "sequenceNo": 0,
                "videoId": "vid-1",
                "type": "play_started",
                "occurredAt": "2026-08-01T10:00:00Z",
                "monotonicMs": 0,
                "payload": {"effectiveDurationSeconds": 200, "durationSource": "PLAYER"},
            },
            {
                "clientEventId": "e2",
                "sessionId": "sess-1",
                "sequenceNo": 1,
                "videoId": "vid-1",
                "type": "progress_tick",
                "occurredAt": "2026-08-01T10:03:00Z",
                "monotonicMs": 180000,
                "payload": {
                    "playedSeconds": 190,
                    "effectiveDurationSeconds": 200,
                    "durationSource": "PLAYER",
                },
            },
        ],
    }
    response = authed_client.post("/api/v1/telemetry/events:batch", json=payload)
    assert response.status_code == 200

    db_session.expire_all()
    row = db_session.get(TrackAffinity, "vid-1")
    assert row is not None
    assert row.plays_all == 1
    assert row.decayed_reward > 0
