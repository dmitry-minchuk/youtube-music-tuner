"""Desired playlist construction (docs/05 section 12).

The list used to be the first N items of a wave, which left the familiar
quota, the per-artist cap and the quality floor entirely to chance. In
practice the gates then refused every playlist, and because a refusal is
returned as a normal 200 the Create button looked like it did nothing.
"""

from __future__ import annotations

import datetime as dt
import math

from sqlalchemy.orm import Session

from app.persistence.models import (
    Artist,
    CandidateEdge,
    LibraryTrackState,
    Track,
    TrackArtist,
    utcnow,
)
from app.publishing.desired_list import build_desired_list
from app.publishing.quality_gates import (
    MAX_TRACKS_PER_ARTIST,
    MIN_ARTIST_RATIO,
    MIN_PUBLISH_SIZE,
)
from app.recommender.rules import QUALITY_EXPECTED_FLOOR
from app.recommender.wave import WaveRequest, generate_wave


def add(db: Session, video_id: str, artist: str, *, liked: bool = False) -> None:
    db.add(Track(video_id=video_id, title=f"Track {video_id}", is_playable=True))
    artist_id = f"UC{artist}"
    if db.get(Artist, artist_id) is None:
        db.add(Artist(artist_id=artist_id, name=artist))
    db.add(TrackArtist(track_id=video_id, artist_id=artist_id, ordinal=0))
    db.flush()
    if liked:
        db.add(LibraryTrackState(video_id=video_id, is_liked=True))
        db.flush()


def build_library(db: Session, likes: int = 40, discoveries: int = 120) -> None:
    for index in range(likes):
        add(db, f"like-{index}", f"Fav{index}", liked=True)
    for index in range(discoveries):
        add(db, f"disc-{index}", f"New{index}")
        db.add(
            CandidateEdge(
                seed_video_id=f"like-{index % likes}",
                candidate_video_id=f"disc-{index}",
                source_type="RADIO",
                source_key="radio",
                rank=1 + index % 5,
                hop=1,
                fetched_at=utcnow(),
                expires_at=utcnow() + dt.timedelta(days=7),
            )
        )
    db.flush()


def test_every_kind_produces_a_publishable_list(db_session) -> None:
    build_library(db_session)
    for kind in ("FAMILIAR", "BALANCE", "DISCOVERY"):
        result = build_desired_list(db_session, kind)
        assert result.passed, (kind, result.failures)
        assert len(result.video_ids) >= MIN_PUBLISH_SIZE


def test_the_list_respects_the_artist_rules(db_session) -> None:
    build_library(db_session)
    result = build_desired_list(db_session, "BALANCE")
    assert result.passed, result.failures

    order = list(result.video_ids)
    artists = {
        track_id: artist_id
        for track_id, artist_id in db_session.execute(
            TrackArtist.__table__.select().with_only_columns(
                TrackArtist.track_id, TrackArtist.artist_id
            )
        ).all()
    }
    sequence = [artists[video_id] for video_id in order]

    assert all(left != right for left, right in zip(sequence, sequence[1:], strict=False))
    assert max(sequence.count(artist) for artist in set(sequence)) <= MAX_TRACKS_PER_ARTIST
    assert len(set(sequence)) >= math.ceil(MIN_ARTIST_RATIO * len(order))


def test_only_tracks_above_the_quality_floor_are_published(db_session) -> None:
    build_library(db_session)
    result = build_desired_list(db_session, "DISCOVERY")
    assert result.passed, result.failures

    pool = generate_wave(
        db_session,
        WaveRequest(temperature=80, length=180, for_publishing=True, random_seed=1),
    )
    quality = {item.video_id: item.quality_expected for item in pool.items}
    for video_id in result.video_ids:
        if video_id in quality:
            assert quality[video_id] >= QUALITY_EXPECTED_FLOOR


def test_a_thin_library_reports_why_instead_of_silently_skipping(db_session) -> None:
    build_library(db_session, likes=3, discoveries=4)
    result = build_desired_list(db_session, "BALANCE")
    assert not result.passed
    assert result.failures
    assert result.video_ids == ()


def test_publishing_mode_keeps_the_freshness_machinery_out(db_session) -> None:
    """A playlist is a snapshot: recency and wave history must not thin it."""
    from app.persistence.models import PlaybackSession

    build_library(db_session)
    for index in range(20):
        db_session.add(
            PlaybackSession(
                session_id=f"heard-{index}",
                video_id=f"like-{index}",
                started_at=utcnow() - dt.timedelta(minutes=index),
                qualified=True,
                played_seconds=120.0,
            )
        )
    db_session.flush()

    streaming = generate_wave(db_session, WaveRequest(temperature=50, length=180, random_seed=3))
    publishing = generate_wave(
        db_session,
        WaveRequest(temperature=50, length=180, random_seed=3, for_publishing=True),
    )

    heard = {f"like-{index}" for index in range(20)}
    in_streaming = heard & {item.video_id for item in streaming.items}
    in_publishing = heard & {item.video_id for item in publishing.items}
    assert len(in_publishing) > len(in_streaming)


def test_a_liked_track_is_not_discounted_for_appearing_in_a_radio(db_session) -> None:
    """A like is a root of the graph, not a distant candidate."""
    add(db_session, "root", "Alpha", liked=True)
    add(db_session, "other", "Beta", liked=True)
    db_session.add(
        CandidateEdge(
            seed_video_id="other",
            candidate_video_id="root",
            source_type="RADIO",
            source_key="radio",
            rank=20,
            hop=2,
            fetched_at=utcnow(),
            expires_at=utcnow() + dt.timedelta(days=7),
        )
    )
    db_session.flush()

    wave = generate_wave(db_session, WaveRequest(temperature=50, length=10, random_seed=1))
    scores = {item.video_id: item.quality_expected for item in wave.items}
    assert scores["root"] >= QUALITY_EXPECTED_FLOOR
    assert scores["root"] == scores["other"]
