"""Revision-aware rating commands (docs/03 section 6, docs/11 section 4)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.catalog import Rating
from app.jobs import queue, worker
from app.persistence.models import Job, LibraryTrackState, Track
from tests.fakes import FakeCatalog


def _seed_track(session, video_id: str = "v1") -> None:
    session.add(Track(video_id=video_id, title="One"))
    session.flush()


def test_rapid_toggles_collapse_into_one_command(db_session) -> None:
    from app.api.library import RatingRequest, set_rating

    _seed_track(db_session)

    for state in ("LIKE", "DISLIKE", "LIKE", "DISLIKE"):
        set_rating("v1", RatingRequest(desiredState=state), db_session)

    jobs = db_session.scalars(select(Job).where(Job.dedupe_key == "rating:v1")).all()
    assert len(jobs) == 1
    assert jobs[0].payload_json["desiredState"] == "DISLIKE"
    assert db_session.get(LibraryTrackState, "v1").rating_revision == 4


def test_dedupe_key_has_no_state_in_it(db_session) -> None:
    from app.api.library import RatingRequest, set_rating

    _seed_track(db_session)
    set_rating("v1", RatingRequest(desiredState="LIKE"), db_session)

    job = db_session.scalar(select(Job))
    assert job.dedupe_key == "rating:v1"
    assert "LIKE" not in job.dedupe_key


def test_worker_sends_the_newest_state_and_confirms_it(db_session) -> None:
    from app.api.library import RatingRequest, set_rating

    _seed_track(db_session)
    set_rating("v1", RatingRequest(desiredState="DISLIKE"), db_session)

    job = db_session.scalar(select(Job))
    catalog = FakeCatalog()
    worker.execute(db_session, job, catalog)

    assert catalog.ratings == [("v1", Rating.DISLIKE)]
    state = db_session.get(LibraryTrackState, "v1")
    assert state.rating_sync_status == "SYNCED"
    assert state.rating_synced_revision == state.rating_revision


def test_state_changed_mid_flight_is_not_confirmed_and_reschedules(db_session) -> None:
    """A like that lands after a newer dislike must never become final."""
    from app.api.library import RatingRequest, set_rating

    _seed_track(db_session)
    set_rating("v1", RatingRequest(desiredState="LIKE"), db_session)
    job = db_session.scalar(select(Job))

    catalog = FakeCatalog()

    def change_mind_during_call(_video_id, _rating):
        state = db_session.get(LibraryTrackState, "v1")
        state.rating_revision += 1
        state.desired_rating = "DISLIKE"
        db_session.flush()

    catalog.rate_hook = change_mind_during_call
    worker.execute(db_session, job, catalog)

    state = db_session.get(LibraryTrackState, "v1")
    assert state.rating_sync_status != "SYNCED"
    assert state.desired_rating == "DISLIKE"

    successor = queue.find_active(db_session, "rating:v1")
    assert successor is not None
    assert successor.job_id != job.job_id


def test_api_returns_revision_and_sync_status(authed_client: TestClient, db_session) -> None:
    _seed_track(db_session)
    db_session.commit()

    response = authed_client.put("/api/v1/tracks/v1/rating", json={"desiredState": "LIKE"})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "videoId": "v1",
        "desiredState": "LIKE",
        "revision": 1,
        "syncStatus": "PENDING",
        "vetoed": False,
    }


def test_rating_unknown_track_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.put("/api/v1/tracks/nope/rating", json={"desiredState": "LIKE"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


# -- veto (docs/05 s.11, docs/08 s.3) ---------------------------------------


def test_veto_round_trip(authed_client: TestClient, db_session) -> None:
    """POST sets it with the artist snapshot, rating reads it, DELETE clears."""
    from app.persistence.models import Artist, TasteVeto, TrackArtist

    _seed_track(db_session)
    db_session.add(Artist(artist_id="UCOne", name="One"))
    db_session.add(TrackArtist(track_id="v1", artist_id="UCOne", ordinal=0))
    db_session.commit()

    response = authed_client.post("/api/v1/tracks/v1/veto")
    assert response.status_code == 200
    assert response.json() == {"videoId": "v1", "vetoed": True}

    db_session.expire_all()
    row = db_session.get(TasteVeto, "v1")
    assert row is not None
    assert row.artist_id is not None

    rating = authed_client.get("/api/v1/tracks/v1/rating")
    assert rating.json()["vetoed"] is True

    response = authed_client.delete("/api/v1/tracks/v1/veto")
    assert response.json() == {"videoId": "v1", "vetoed": False}
    db_session.expire_all()
    assert db_session.get(TasteVeto, "v1") is None


def test_veto_unknown_track_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.post("/api/v1/tracks/nope/veto")
    assert response.status_code == 400


def test_veto_is_reported_even_without_library_state(authed_client: TestClient, db_session) -> None:
    """A veto can exist for a track that has no rating row at all."""
    from app.persistence.models import TasteVeto, Track

    db_session.add(Track(video_id="loose", title="Loose", is_playable=True))
    db_session.flush()
    db_session.add(TasteVeto(video_id="loose"))
    db_session.commit()

    rating = authed_client.get("/api/v1/tracks/loose/rating")
    assert rating.status_code == 200
    assert rating.json()["vetoed"] is True


def test_veto_set_event_aggregates_as_explicit_dislike() -> None:
    from app.player.aggregation import SessionAccumulator, fold_event

    accumulator = SessionAccumulator(session_id="s1", video_id="v1")
    for event in (
        {"type": "track_cued"},
        {"type": "play_started"},
        {"type": "veto_set"},
    ):
        fold_event(accumulator, event)
    assert accumulator.explicit_rating == "DISLIKE"
