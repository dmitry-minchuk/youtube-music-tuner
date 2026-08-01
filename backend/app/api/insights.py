"""Insights endpoints (docs/08 section 6, docs/06 section 7).

Deterministic facts and counts only — no generated narrative. Metrics that
depend on a ratio exclude absolute-time sessions so the baseline stays
comparable (docs/01 section 10).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import errors
from app.integrations.youtube_music.ledger import (
    GLOBAL_PLAYLIST_REQUESTS_PER_DAY,
    LIBRARY_SYNC_PER_DAY,
    CallBudget,
)
from app.persistence.database import get_session
from app.persistence.models import (
    ApiCallLedger,
    Artist,
    ModelSnapshot,
    PlaybackSession,
    QueueGeneration,
    QueueItem,
    TrackArtist,
    utcnow,
)
from app.recommender.learning_state import (
    BOOTSTRAP_MIN_NEGATIVE,
    BOOTSTRAP_MIN_POSITIVE,
    BOOTSTRAP_MIN_SESSIONS,
    BOOTSTRAP_MIN_TRACKS,
    CLEAN_BASELINE_SESSIONS,
    learning_status,
    progress_label,
)

router = APIRouter(prefix="/api/v1", tags=["insights"])

PERIODS = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/insights/learning-status")
def learning_status_endpoint(db: Session = Depends(get_session)) -> dict[str, Any]:
    status = learning_status(db)
    return {
        "phase": status.phase.value,
        "label": progress_label(status),
        "servingPolicy": status.serving_policy,
        "servingModelId": status.serving_model_id,
        "shadowModelId": status.shadow_model_id,
        "qualifiedSessions": status.qualified_sessions,
        "distinctTracks": status.distinct_tracks,
        "positiveSessions": status.positive_sessions,
        "negativeSessions": status.negative_sessions,
        "thresholds": {
            "sessions": BOOTSTRAP_MIN_SESSIONS,
            "tracks": BOOTSTRAP_MIN_TRACKS,
            "positive": BOOTSTRAP_MIN_POSITIVE,
            "negative": BOOTSTRAP_MIN_NEGATIVE,
            "cleanBaseline": CLEAN_BASELINE_SESSIONS,
        },
        "bootstrapReady": status.bootstrap_ready,
        "baselineComplete": status.baseline_complete,
    }


@router.get("/insights/summary")
def insights_summary(
    period: str = Query("30d"), db: Session = Depends(get_session)
) -> dict[str, Any]:
    if period not in PERIODS:
        raise errors.ValidationFailed("period must be one of 7d, 30d, 90d")
    since = utcnow() - dt.timedelta(days=PERIODS[period])

    # Ratio-based rates only; unknown-duration sessions are reported apart.
    ratio_sessions = select(PlaybackSession).where(
        PlaybackSession.qualified.is_(True),
        PlaybackSession.started_at >= since,
        PlaybackSession.classification_basis == "RATIO",
    )
    total = db.scalar(select(func.count()).select_from(ratio_sessions.subquery())) or 0
    early_skips = (
        db.scalar(
            select(func.count()).select_from(
                ratio_sessions.where(PlaybackSession.early_skip.is_(True)).subquery()
            )
        )
        or 0
    )
    completions = (
        db.scalar(
            select(func.count()).select_from(
                ratio_sessions.where(PlaybackSession.completed.is_(True)).subquery()
            )
        )
        or 0
    )
    absolute_total = (
        db.scalar(
            select(func.count())
            .select_from(PlaybackSession)
            .where(
                PlaybackSession.qualified.is_(True),
                PlaybackSession.started_at >= since,
                PlaybackSession.classification_basis == "ABSOLUTE_TIME",
            )
        )
        or 0
    )

    familiar = (
        db.scalar(
            select(func.count())
            .select_from(QueueItem)
            .join(QueueGeneration, QueueGeneration.generation_id == QueueItem.generation_id)
            .where(
                QueueGeneration.created_at >= since,
                QueueItem.familiarity == "FAMILIAR",
                QueueItem.played_at.is_not(None),
            )
        )
        or 0
    )
    discovery = (
        db.scalar(
            select(func.count())
            .select_from(QueueItem)
            .join(QueueGeneration, QueueGeneration.generation_id == QueueItem.generation_id)
            .where(
                QueueGeneration.created_at >= since,
                QueueItem.familiarity == "DISCOVERY",
                QueueItem.played_at.is_not(None),
            )
        )
        or 0
    )

    return {
        "period": period,
        "sampleSize": total,
        "earlySkipRate": round(early_skips / total, 4) if total else None,
        "completionRate": round(completions / total, 4) if total else None,
        "absoluteTimeSessions": absolute_total,
        "playedFamiliar": familiar,
        "playedDiscovery": discovery,
        "topPositiveArtists": _top_artists(db, since, positive=True),
        "topNegativeArtists": _top_artists(db, since, positive=False),
        "lastTrainingAt": _last_training(db),
    }


def _top_artists(db: Session, since: dt.datetime, *, positive: bool, limit: int = 5) -> list[dict]:
    condition = PlaybackSession.reward > 0 if positive else PlaybackSession.reward < 0
    rows = db.execute(
        select(Artist.name, func.count().label("plays"), func.avg(PlaybackSession.reward))
        .join(TrackArtist, TrackArtist.artist_id == Artist.artist_id)
        .join(PlaybackSession, PlaybackSession.video_id == TrackArtist.track_id)
        .where(
            PlaybackSession.qualified.is_(True),
            PlaybackSession.started_at >= since,
            PlaybackSession.reward.is_not(None),
            TrackArtist.ordinal == 0,
            condition,
        )
        .group_by(Artist.name)
        .order_by(func.count().desc())
        .limit(limit)
    ).all()
    return [
        {"artist": name, "sessions": plays, "meanReward": round(float(mean or 0.0), 3)}
        for name, plays, mean in rows
    ]


def _last_training(db: Session) -> str | None:
    row = db.scalar(
        select(ModelSnapshot.created_at).order_by(ModelSnapshot.created_at.desc()).limit(1)
    )
    return row.isoformat() + "Z" if row else None


@router.get("/tracks/{video_id}/explanation")
def track_explanation(
    video_id: str, generationId: str, db: Session = Depends(get_session)
) -> dict[str, Any]:
    item = db.scalar(
        select(QueueItem).where(
            QueueItem.generation_id == generationId, QueueItem.video_id == video_id
        )
    )
    if item is None:
        raise errors.ValidationFailed("track is not part of that generation")
    generation = db.get(QueueGeneration, generationId)

    return {
        "videoId": video_id,
        "generationId": generationId,
        "reasonCodes": item.reason_codes_json.get("codes", []),
        "score": round(item.score, 4),
        "qualityExpected": round(item.quality_expected, 4),
        "familiarity": item.familiarity,
        "servingPolicy": generation.serving_policy if generation else None,
        "qualityScoreSource": generation.quality_score_source if generation else None,
    }


@router.get("/diagnostics/api-budget")
def api_budget(db: Session = Depends(get_session)) -> dict[str, Any]:
    now = utcnow()
    budget = CallBudget(db, now)
    circuit = budget.circuit_state()
    day_ago = now - dt.timedelta(days=1)

    calls_today = (
        db.scalar(
            select(func.count())
            .select_from(ApiCallLedger)
            .where(ApiCallLedger.started_at >= day_ago)
        )
        or 0
    )

    return {
        "librarySync": {
            "used": budget.library_sync_calls_today(),
            "limit": LIBRARY_SYNC_PER_DAY,
        },
        "playlistRequests": {
            "used": budget.global_playlist_requests_today(),
            "limit": GLOBAL_PLAYLIST_REQUESTS_PER_DAY,
        },
        "externalCallsLast24h": calls_today,
        "circuit": {
            "open": circuit.open,
            "reason": circuit.reason,
            "retryAfter": circuit.retry_after.isoformat() + "Z" if circuit.retry_after else None,
        },
    }
