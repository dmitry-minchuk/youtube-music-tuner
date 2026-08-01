"""Telemetry ingestion contract (docs/08 section 5, docs/11 section 4)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def event(
    session_id: str,
    sequence: int,
    event_type: str,
    *,
    client_event_id: str | None = None,
    **payload,
) -> dict:
    return {
        "clientEventId": client_event_id or str(uuid.uuid4()),
        "sessionId": session_id,
        "sequenceNo": sequence,
        "videoId": "vid-1",
        "type": event_type,
        "occurredAt": "2026-08-01T18:42:10.123Z",
        "monotonicMs": sequence * 1000,
        "payload": payload,
    }


def post(client: TestClient, events: list[dict]) -> dict:
    response = client.post(
        "/api/v1/telemetry/events:batch", json={"schemaVersion": 1, "events": events}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_batch_is_accepted_and_aggregated(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    body = post(
        authed_client,
        [
            event(session_id, 1, "play_started"),
            event(
                session_id,
                2,
                "progress_tick",
                playedSeconds=45,
                effectiveDurationSeconds=200,
                durationSource="PLAYER",
            ),
        ],
    )
    assert body["accepted"] == 2
    assert body["duplicates"] == 0

    summary = authed_client.get(f"/api/v1/telemetry/sessions/{session_id}").json()
    assert summary["playedSeconds"] == 45
    assert summary["qualified"] is True
    assert summary["classificationBasis"] == "RATIO"


def test_replayed_batch_is_a_no_op(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    events = [
        event(session_id, 1, "play_started", client_event_id="fixed-1"),
        event(
            session_id,
            2,
            "progress_tick",
            client_event_id="fixed-2",
            playedSeconds=30,
            effectiveDurationSeconds=200,
            durationSource="PLAYER",
        ),
    ]
    post(authed_client, events)
    second = post(authed_client, events)

    assert second["accepted"] == 0
    assert second["duplicates"] == 2

    summary = authed_client.get(f"/api/v1/telemetry/sessions/{session_id}").json()
    assert summary["playedSeconds"] == 30  # not doubled


def test_one_invalid_event_does_not_reject_the_batch(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    body = post(
        authed_client,
        [
            event(session_id, 1, "play_started"),
            event(session_id, 2, "teleport_to_mars"),
            event(session_id, 3, "progress_tick", playedSeconds=12),
        ],
    )
    assert body["accepted"] == 2
    assert len(body["rejected"]) == 1
    assert body["rejected"][0]["code"] == "UNKNOWN_EVENT_TYPE"


def test_out_of_order_delivery_is_folded_by_sequence(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    post(
        authed_client,
        [
            event(
                session_id,
                3,
                "progress_tick",
                playedSeconds=45,
                effectiveDurationSeconds=200,
                durationSource="PLAYER",
            ),
            event(session_id, 1, "play_started"),
        ],
    )
    summary = authed_client.get(f"/api/v1/telemetry/sessions/{session_id}").json()
    assert summary["playedSeconds"] == 45


def test_explicit_next_produces_a_negative_reward(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    post(
        authed_client,
        [
            event(
                session_id,
                1,
                "progress_tick",
                playedSeconds=15,
                effectiveDurationSeconds=200,
                durationSource="PLAYER",
            ),
            event(session_id, 2, "next_clicked"),
        ],
    )
    summary = authed_client.get(f"/api/v1/telemetry/sessions/{session_id}").json()
    assert summary["earlySkip"] is True
    assert summary["reward"] < 0


def test_page_close_produces_no_negative_reward(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    post(
        authed_client,
        [
            event(
                session_id,
                1,
                "progress_tick",
                playedSeconds=15,
                effectiveDurationSeconds=200,
                durationSource="PLAYER",
            ),
            event(session_id, 2, "page_closing"),
        ],
    )
    summary = authed_client.get(f"/api/v1/telemetry/sessions/{session_id}").json()
    assert summary["earlySkip"] is False
    assert summary["reward"] == 0.0


def test_batch_size_limit_is_enforced(authed_client: TestClient) -> None:
    session_id = str(uuid.uuid4())
    events = [event(session_id, index, "progress_tick") for index in range(101)]
    response = authed_client.post(
        "/api/v1/telemetry/events:batch", json={"schemaVersion": 1, "events": events}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_unsupported_schema_version_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/v1/telemetry/events:batch",
        json={"schemaVersion": 99, "events": [event("s", 1, "play_started")]},
    )
    assert response.status_code == 400


def test_telemetry_requires_a_session_token(client: TestClient) -> None:
    response = client.post(
        "/api/v1/telemetry/events:batch",
        json={"schemaVersion": 1, "events": [event("s", 1, "play_started")]},
    )
    assert response.status_code == 403


def test_permanent_embed_error_takes_the_track_out_of_rotation(
    authed_client: TestClient, db_session
) -> None:
    """Error 150 means the owner disallowed embedding — it will never play."""
    from app.persistence.models import Track

    db_session.add(Track(video_id="vid-1", title="Blocked", is_playable=True))
    db_session.commit()

    session_id = str(uuid.uuid4())
    post(authed_client, [event(session_id, 1, "player_error", errorCode=150)])

    db_session.expire_all()
    assert db_session.get(Track, "vid-1").is_playable is False


def test_transient_error_leaves_the_track_playable(authed_client: TestClient, db_session) -> None:
    """Code 2 is a bad parameter, not a permanent block."""
    from app.persistence.models import Track

    db_session.add(Track(video_id="vid-1", title="Fine", is_playable=True))
    db_session.commit()

    post(authed_client, [event(str(uuid.uuid4()), 1, "player_error", errorCode=2)])

    db_session.expire_all()
    assert db_session.get(Track, "vid-1").is_playable is True
