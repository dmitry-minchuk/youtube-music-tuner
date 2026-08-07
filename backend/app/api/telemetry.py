"""Telemetry ingestion (docs/04 section 6, docs/08 section 5).

Events are idempotent by ``clientEventId``; one invalid event never rejects
the whole batch. After each accepted batch the affected sessions are
re-aggregated from their raw events, so a replay cannot double count.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import errors
from app.persistence.database import get_session
from app.persistence.models import (
    FeatureSnapshot,
    PlaybackSession,
    TelemetryEvent,
    Track,
    utcnow,
)
from app.player.aggregation import (
    AGGREGATION_VERSION,
    SessionAccumulator,
    fold_event,
    summarize,
)
from app.recommender.affinity import refresh_for_track

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["telemetry"])

MAX_EVENTS_PER_BATCH = 100
SUPPORTED_SCHEMA_VERSION = 1

# YouTube IFrame error codes. 101 and 150 both mean "the owner disallowed
# embedded playback" — a permanent property of the track, not a hiccup, so
# the track stops being queued instead of failing again every day.
PERMANENT_EMBED_ERRORS = frozenset({101, 150})

KNOWN_EVENT_TYPES = frozenset(
    {
        "track_cued",
        "play_started",
        "play_resumed",
        "paused",
        "buffering_started",
        "progress_tick",
        "seek_forward",
        "seek_backward",
        "next_clicked",
        "previous_clicked",
        "ended",
        "replay_started",
        "like_set",
        "dislike_set",
        "veto_set",
        "player_error",
        "visibility_changed",
        "page_closing",
    }
)


class TelemetryEventIn(BaseModel):
    clientEventId: str = Field(min_length=1, max_length=64)
    sessionId: str = Field(min_length=1, max_length=64)
    sequenceNo: int = Field(ge=0)
    videoId: str = Field(min_length=1, max_length=64)
    type: str = Field(min_length=1, max_length=32)
    occurredAt: dt.datetime
    monotonicMs: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    schemaVersion: Literal[1] = 1
    events: Annotated[list[TelemetryEventIn], Field(min_length=1, max_length=MAX_EVENTS_PER_BATCH)]


def _naive_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is not None:
        return value.astimezone(dt.UTC).replace(tzinfo=None)
    return value


def reaggregate_session(db: Session, session_id: str) -> PlaybackSession | None:
    """Recompute a session summary from its raw events."""
    rows = db.scalars(
        select(TelemetryEvent)
        .where(TelemetryEvent.session_id == session_id)
        .order_by(TelemetryEvent.sequence_no)
    ).all()
    if not rows:
        return None

    accumulator = SessionAccumulator(session_id=session_id, video_id=rows[0].video_id)
    for row in rows:
        fold_event(
            accumulator,
            {
                "type": row.event_type,
                "payload": row.payload_json,
                "occurred_at": row.occurred_at_client,
            },
        )
    summary = summarize(accumulator)

    record = db.get(PlaybackSession, session_id)
    if record is None:
        record = PlaybackSession(session_id=session_id, video_id=summary.video_id)
        db.add(record)

    record.video_id = summary.video_id
    record.queue_id = accumulator.queue_id
    record.generation_id = accumulator.generation_id
    record.started_at = accumulator.started_at or utcnow()
    record.ended_at = accumulator.last_event_at if summary.termination_reason else None
    record.termination_reason = summary.termination_reason
    record.effective_duration_seconds = summary.effective_duration_seconds
    record.duration_source = summary.duration_source
    record.played_seconds = summary.played_seconds
    record.played_ratio = summary.played_ratio
    record.max_position_seconds = accumulator.max_position_seconds
    record.seek_forward_seconds = accumulator.seek_forward_seconds
    record.seek_backward_seconds = accumulator.seek_backward_seconds
    record.seek_forward_count = accumulator.seek_forward_count
    record.seek_backward_count = accumulator.seek_backward_count
    record.buffered_seconds = accumulator.buffered_seconds
    record.wall_clock_seconds = accumulator.wall_clock_seconds
    record.explicit_rating = summary.explicit_rating
    record.early_skip = summary.early_skip
    record.mid_skip = summary.mid_skip
    record.completed = summary.completed
    record.ended_unqualified = summary.ended_unqualified
    record.large_forward_seek = summary.large_forward_seek
    record.replayed = summary.replayed
    record.classification_basis = summary.classification_basis
    record.qualified = summary.qualified
    record.reward = summary.reward
    record.reward_version = summary.reward_version
    record.source = "TUNER"
    record.aggregation_version = AGGREGATION_VERSION
    record.aggregated_at = utcnow()

    _link_feature_snapshot(db, record)
    db.flush()
    # Fold the session into the ranking aggregates right away: a skip must
    # affect the next track, not tomorrow's batch (docs/05 section 12).
    refresh_for_track(db, record.video_id)
    db.flush()
    return record


def _mark_unplayable_if_permanent(db: Session, video_id: str, payload: dict[str, Any]) -> None:
    """Take a track out of rotation when YouTube refuses to embed it."""
    code = payload.get("errorCode")
    if not isinstance(code, int) or code not in PERMANENT_EMBED_ERRORS:
        return
    track = db.get(Track, video_id)
    if track is not None and track.is_playable:
        track.is_playable = False
        db.flush()
        logger.info(
            "track marked unplayable in embedded player",
            extra={"operation": "telemetry", "video_id": video_id, "error_code": code},
        )


def _link_feature_snapshot(db: Session, record: PlaybackSession) -> None:
    """Bind the vector used at selection time to the session it produced.

    The model must learn from the features as they were when the track was
    chosen, not from values recomputed later (docs/07 section 4).
    """
    if record.generation_id is None:
        return
    snapshot = db.scalar(
        select(FeatureSnapshot).where(
            FeatureSnapshot.generation_id == record.generation_id,
            FeatureSnapshot.video_id == record.video_id,
        )
    )
    if snapshot is not None and snapshot.session_id is None:
        snapshot.session_id = record.session_id
        snapshot.consumed_at = utcnow()


@router.post("/telemetry/events:batch")
def ingest_batch(payload: dict[str, Any], db: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        batch = TelemetryBatch.model_validate(payload)
    except ValidationError as exc:
        raise errors.ValidationFailed("telemetry batch is not valid") from exc

    accepted = 0
    duplicates = 0
    rejected: list[dict[str, str]] = []
    touched_sessions: set[str] = set()

    for event in batch.events:
        if event.type not in KNOWN_EVENT_TYPES:
            rejected.append({"clientEventId": event.clientEventId, "code": "UNKNOWN_EVENT_TYPE"})
            continue

        existing = db.scalar(
            select(TelemetryEvent).where(TelemetryEvent.client_event_id == event.clientEventId)
        )
        if existing is not None:
            duplicates += 1
            touched_sessions.add(event.sessionId)
            continue

        row = TelemetryEvent(
            client_event_id=event.clientEventId,
            session_id=event.sessionId,
            sequence_no=event.sequenceNo,
            event_type=event.type,
            video_id=event.videoId,
            occurred_at_client=_naive_utc(event.occurredAt),
            received_at_server=utcnow(),
            monotonic_ms=event.monotonicMs,
            schema_version=batch.schemaVersion,
            payload_json=event.payload,
        )
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            # A concurrent duplicate, or a reused (session, sequence) pair.
            duplicates += 1
            touched_sessions.add(event.sessionId)
            continue

        accepted += 1
        touched_sessions.add(event.sessionId)
        if event.type == "player_error":
            _mark_unplayable_if_permanent(db, event.videoId, event.payload)

    for session_id in touched_sessions:
        reaggregate_session(db, session_id)

    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "serverTime": utcnow().isoformat() + "Z",
    }


@router.post("/telemetry/events:beacon")
def ingest_beacon(payload: dict[str, Any], db: Session = Depends(get_session)) -> dict[str, Any]:
    """Same ingestion, reachable from ``navigator.sendBeacon`` on pagehide.

    sendBeacon cannot set the CSRF header, so this path is authenticated by
    the SameSite=Strict session cookie plus an exact Origin check in the
    guard middleware.
    """
    return ingest_batch(payload, db)


@router.get("/telemetry/sessions/{session_id}")
def session_summary(session_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    record = db.get(PlaybackSession, session_id)
    if record is None:
        raise errors.ValidationFailed("unknown session")
    return {
        "sessionId": record.session_id,
        "videoId": record.video_id,
        "playedSeconds": record.played_seconds,
        "playedRatio": record.played_ratio,
        "classificationBasis": record.classification_basis,
        "earlySkip": record.early_skip,
        "midSkip": record.mid_skip,
        "completed": record.completed,
        "endedUnqualified": record.ended_unqualified,
        "largeForwardSeek": record.large_forward_seek,
        "replayed": record.replayed,
        "explicitRating": record.explicit_rating,
        "qualified": record.qualified,
        "reward": record.reward,
        "rewardVersion": record.reward_version,
        "durationSource": record.duration_source,
    }
