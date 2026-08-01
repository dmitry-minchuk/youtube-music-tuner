"""Reconnecting must clear a circuit opened by auth failures.

Without this the app deadlocks after the first start: the scheduler tries to
sync, fails with YTM_AUTH_REQUIRED, opens the circuit, and every later call
is refused before it can prove that auth works again.
"""

from __future__ import annotations

from app.integrations.youtube_music.ledger import CallBudget, SqlCallRecorder
from app.persistence.models import ApiCallLedger, utcnow


def _fail(db, code: str = "YTM_AUTH_REQUIRED") -> None:
    db.add(
        ApiCallLedger(
            operation="get_liked_songs",
            outcome="FAILED",
            status_class=code,
            started_at=utcnow(),
        )
    )
    db.flush()


def test_auth_failure_opens_the_circuit(db_session) -> None:
    _fail(db_session)
    assert CallBudget(db_session).circuit_state().open is True


def test_a_later_successful_call_closes_the_circuit(db_session) -> None:
    _fail(db_session)
    assert CallBudget(db_session).circuit_state().open is True

    SqlCallRecorder(db_session).record(
        operation="get_account_info",
        duration_ms=50,
        outcome="SUCCESS",
        is_mutation=False,
    )

    state = CallBudget(db_session).circuit_state()
    assert state.open is False
    assert state.reason is None


def test_the_cli_auth_check_is_written_to_the_ledger(db_session) -> None:
    """The account check after the device flow must be a recorded call."""
    SqlCallRecorder(db_session).record(
        operation="get_account_info",
        duration_ms=42,
        outcome="SUCCESS",
        is_mutation=False,
    )
    row = db_session.query(ApiCallLedger).one()
    assert row.operation == "get_account_info"
    assert row.outcome == "SUCCESS"
    assert row.is_mutation is False
