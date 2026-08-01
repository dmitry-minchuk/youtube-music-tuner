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
    }


def test_rating_unknown_track_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.put("/api/v1/tracks/nope/rating", json={"desiredState": "LIKE"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
