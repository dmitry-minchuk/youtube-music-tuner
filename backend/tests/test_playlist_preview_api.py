"""Reviewable playlist preview (docs/03 section 8, docs/05 section 12).

Preview used to return counters and bare video ids, so there was no way to see
what a playlist contained, no way to hear it, and no way to ask for a different
selection — publishing was effectively blind.
"""

from __future__ import annotations

import datetime as dt

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.persistence.models import (
    Artist,
    CandidateEdge,
    LibraryTrackState,
    ManagedPlaylist,
    Track,
    TrackArtist,
    utcnow,
)
from app.publishing.service import marker_for


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


def build_library(db: Session, likes: int = 60, discoveries: int = 240) -> None:
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
    db.add(
        ManagedPlaylist(
            managed_playlist_id="mp-balance",
            playlist_id="PLexisting",
            kind="BALANCE",
            instance_id="inst-1",
            ownership_marker=marker_for("inst-1"),
            temperature=50,
            status="ACTIVE",
        )
    )
    db.commit()


def preview(client: TestClient, regenerate: bool = False) -> dict:
    response = client.post(
        "/api/v1/managed-playlists/BALANCE/plan", json={"regenerate": regenerate}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_preview_lists_the_actual_tracks(authed_client: TestClient, db_session) -> None:
    build_library(db_session)
    body = preview(authed_client)

    assert body["status"] == "READY"
    assert len(body["tracks"]) == len(body["videoIds"])
    first = body["tracks"][0]
    assert first["title"]
    assert first["artists"]
    assert first["familiarity"] in {"FAMILIAR", "DISCOVERY"}


def test_asking_twice_returns_the_reviewed_list(authed_client: TestClient, db_session) -> None:
    """Publish writes what was shown, so the preview must not drift."""
    build_library(db_session)
    first = preview(authed_client)
    second = preview(authed_client)

    assert [t["videoId"] for t in first["tracks"]] == [t["videoId"] for t in second["tracks"]]
    assert first["randomSeed"] == second["randomSeed"]


def test_regenerate_offers_a_different_selection(authed_client: TestClient, db_session) -> None:
    build_library(db_session)
    first = preview(authed_client)
    again = preview(authed_client, regenerate=True)

    assert again["randomSeed"] != first["randomSeed"]
    order_before = [t["videoId"] for t in first["tracks"]]
    order_after = [t["videoId"] for t in again["tracks"]]
    assert order_before != order_after
    # And it sticks: the regenerated list becomes the reviewed one.
    assert [t["videoId"] for t in preview(authed_client)["tracks"]] == order_after


def test_the_reviewed_list_is_what_gets_published(authed_client: TestClient, db_session) -> None:
    build_library(db_session)
    reviewed = [t["videoId"] for t in preview(authed_client)["tracks"]]

    # Whether the write itself succeeds against the fake remote is beside the
    # point here: what matters is that the list handed to the writer is the
    # reviewed one and never a freshly generated substitute.
    authed_client.post("/api/v1/managed-playlists/BALANCE/publish")

    db_session.expire_all()
    manifest = db_session.get(ManagedPlaylist, "mp-balance")
    assert manifest is not None
    stored = manifest.proposed_desired_json
    assert stored is not None
    assert [track["videoId"] for track in stored["tracks"]] == reviewed
