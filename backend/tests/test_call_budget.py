"""Local call budgets and the circuit breaker (docs/03 section 9)."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.integrations.youtube_music.ledger import (
    GLOBAL_PLAYLIST_REQUESTS_PER_DAY,
    LIBRARY_SYNC_PER_DAY,
    MAX_PLAYLIST_REQUESTS_PER_WINDOW,
    BudgetExceeded,
    CallBudget,
    SqlCallRecorder,
)
from app.persistence.models import ApiCallLedger, utcnow


def _record(session, operation: str, **kwargs) -> None:
    defaults = {
        "provider": "ytmusic",
        "operation": operation,
        "outcome": "SUCCESS",
        "is_mutation": False,
        "counts_against_automatic_budget": True,
        "started_at": utcnow(),
    }
    defaults.update(kwargs)
    session.add(ApiCallLedger(**defaults))
    session.flush()


def test_budget_constants_match_the_specification() -> None:
    # 1 fresh read + 15 mutations + 1 verify, times three managed playlists.
    assert MAX_PLAYLIST_REQUESTS_PER_WINDOW == 17
    assert GLOBAL_PLAYLIST_REQUESTS_PER_DAY == 51


def test_library_sync_budget_allows_four_per_day(db_session) -> None:
    budget = CallBudget(db_session)
    for _ in range(LIBRARY_SYNC_PER_DAY):
        budget.check_library_sync()
        _record(db_session, "get_liked_songs")

    with pytest.raises(BudgetExceeded) as excinfo:
        CallBudget(db_session).check_library_sync()
    assert excinfo.value.budget == "library_sync"


def test_calls_older_than_a_day_do_not_count(db_session) -> None:
    for _ in range(LIBRARY_SYNC_PER_DAY):
        _record(db_session, "get_liked_songs", started_at=utcnow() - dt.timedelta(days=2))
    CallBudget(db_session).check_library_sync()


def test_playlist_window_budget_is_per_playlist(db_session) -> None:
    window_start = utcnow() - dt.timedelta(hours=1)
    for _ in range(MAX_PLAYLIST_REQUESTS_PER_WINDOW):
        _record(db_session, "add_playlist_items", playlist_id="PL1", is_mutation=True)

    with pytest.raises(BudgetExceeded):
        CallBudget(db_session).check_publish_window("PL1", window_start)

    # A different playlist still has its own window.
    CallBudget(db_session).check_publish_window("PL2", window_start)


def test_cleanup_calls_do_not_consume_the_automatic_budget(db_session) -> None:
    window_start = utcnow() - dt.timedelta(hours=1)
    for _ in range(MAX_PLAYLIST_REQUESTS_PER_WINDOW + 5):
        _record(
            db_session,
            "delete_playlist",
            playlist_id="PL1",
            is_mutation=True,
            counts_against_automatic_budget=False,
        )
    CallBudget(db_session).check_publish_window("PL1", window_start)


def test_circuit_is_closed_when_calls_succeed(db_session) -> None:
    _record(db_session, "get_liked_songs")
    assert CallBudget(db_session).circuit_state().open is False


def test_auth_failure_opens_the_circuit_immediately(db_session) -> None:
    _record(db_session, "get_liked_songs", outcome="FAILED", status_class="YTM_AUTH_REQUIRED")
    state = CallBudget(db_session).circuit_state()
    assert state.open is True
    assert state.reason == "YTM_AUTH_REQUIRED"


def test_rate_limit_opens_the_circuit_immediately(db_session) -> None:
    _record(db_session, "search", outcome="FAILED", status_class="YTM_RATE_LIMITED")
    assert CallBudget(db_session).circuit_state().open is True


def test_transient_failures_walk_the_backoff_ladder(db_session) -> None:
    now = utcnow()
    _record(
        db_session,
        "get_playlist",
        outcome="FAILED",
        status_class="YTM_UNAVAILABLE",
        started_at=now,
    )
    state = CallBudget(db_session, now).circuit_state()
    assert state.open is True  # within the first 60 second backoff
    assert state.reason == "BACKOFF"

    later = CallBudget(db_session, now + dt.timedelta(seconds=61)).circuit_state()
    assert later.open is False


def test_four_transient_failures_open_the_circuit(db_session) -> None:
    now = utcnow()
    for index in range(4):
        _record(
            db_session,
            "get_playlist",
            outcome="FAILED",
            status_class="YTM_UNAVAILABLE",
            started_at=now - dt.timedelta(minutes=index),
        )
    # Still inside the one hour analysis window, past the whole backoff ladder.
    state = CallBudget(db_session, now + dt.timedelta(minutes=45)).circuit_state()
    assert state.open is True
    assert state.reason == "REPEATED_FAILURES"


def test_recorder_writes_a_ledger_row(db_session) -> None:
    recorder = SqlCallRecorder(db_session, request_id="req-1")
    recorder.record(
        operation="get_playlist",
        duration_ms=120,
        outcome="SUCCESS",
        is_mutation=False,
        playlist_id="PL1",
    )
    row = db_session.query(ApiCallLedger).one()
    assert row.operation == "get_playlist"
    assert row.duration_ms == 120
    assert row.request_id == "req-1"
    assert row.playlist_id == "PL1"


def test_sync_endpoint_reports_circuit_open(authed_client: TestClient, db_session) -> None:
    _record(db_session, "get_liked_songs", outcome="FAILED", status_class="YTM_AUTH_REQUIRED")
    db_session.commit()

    response = authed_client.post("/api/v1/sync")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CIRCUIT_OPEN"
