"""Model training job (docs/05 sections 8, 12 and 13).

Trains on the feature vector stored at selection time, never on features
recomputed from later data. A new snapshot enters SHADOW; it may only become
ACTIVE once the clean baseline has closed and every safety gate passes.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    FeatureSnapshot,
    LibraryTrackState,
    ModelSnapshot,
    PlaybackSession,
    TrackArtist,
    utcnow,
)
from app.recommender.features import FEATURE_SCHEMA_VERSION, FeatureVector
from app.recommender.learning_state import (
    RETRAIN_MIN_NEW_SESSIONS,
    learning_status,
    may_activate,
)
from app.recommender.linucb import ALGORITHM, LinUcbModel, mean_expected_reward, train
from app.recommender.safety_gates import evaluate_snapshot

logger = logging.getLogger(__name__)

MIN_RETRAIN_INTERVAL = dt.timedelta(minutes=60)
KEEP_SNAPSHOTS = 10


@dataclass(frozen=True, slots=True)
class TrainingResult:
    model_id: str | None
    status: str
    samples: int
    offline_mean_reward: float
    gate_failures: tuple[str, ...] = ()
    skipped_reason: str | None = None


def collect_samples(db: Session) -> list[tuple[np.ndarray, float, str]]:
    """(features at selection time, reward, video_id) for qualified sessions."""
    rows = db.execute(
        select(FeatureSnapshot, PlaybackSession)
        .join(PlaybackSession, PlaybackSession.session_id == FeatureSnapshot.session_id)
        .where(
            PlaybackSession.qualified.is_(True),
            PlaybackSession.reward.is_not(None),
            FeatureSnapshot.feature_schema_version == FEATURE_SCHEMA_VERSION,
        )
        .order_by(PlaybackSession.started_at)
    ).all()

    samples: list[tuple[np.ndarray, float, str]] = []
    for snapshot, session in rows:
        vector = FeatureVector.from_dict(snapshot.features_json)
        samples.append((vector.to_array(), float(session.reward), session.video_id))
    return samples


def _latest_snapshot(db: Session, status: str) -> ModelSnapshot | None:
    return db.scalar(
        select(ModelSnapshot)
        .where(ModelSnapshot.status == status)
        .order_by(ModelSnapshot.created_at.desc())
        .limit(1)
    )


def _should_retrain(db: Session, sample_count: int, now: dt.datetime) -> str | None:
    latest = db.scalar(select(ModelSnapshot).order_by(ModelSnapshot.created_at.desc()).limit(1))
    if latest is None:
        return None
    if now - latest.created_at < MIN_RETRAIN_INTERVAL:
        return "TOO_SOON"
    trained_through = latest.training_counts_json.get("samples", 0)
    if sample_count - int(trained_through) < RETRAIN_MIN_NEW_SESSIONS:
        return "NOT_ENOUGH_NEW_SESSIONS"
    return None


def run_model_training(db: Session, *, force: bool = False) -> TrainingResult:
    now = utcnow()
    status = learning_status(db)

    if not status.bootstrap_ready and not force:
        return TrainingResult(None, "SKIPPED", 0, 0.0, skipped_reason="BOOTSTRAP_NOT_READY")

    samples = collect_samples(db)
    if not samples:
        return TrainingResult(None, "SKIPPED", 0, 0.0, skipped_reason="NO_FEATURE_SNAPSHOTS")

    if not force:
        reason = _should_retrain(db, len(samples), now)
        if reason:
            return TrainingResult(None, "SKIPPED", len(samples), 0.0, skipped_reason=reason)

    training_pairs = [(features, reward) for features, reward, _ in samples]
    model = train(training_pairs)
    offline_mean = mean_expected_reward(model, training_pairs)

    current = _latest_snapshot(db, "ACTIVE")
    current_mean = float(current.metrics_json.get("offlineMeanReward", 0.0)) if current else 0.0

    top_video_ids = [
        video_id for _, _, video_id in sorted(samples, key=lambda item: item[1], reverse=True)[:50]
    ]
    artists: dict[str, str] = {
        track_id: artist_id
        for track_id, artist_id in db.execute(
            select(TrackArtist.track_id, TrackArtist.artist_id).where(
                TrackArtist.track_id.in_(top_video_ids), TrackArtist.ordinal == 0
            )
        ).all()
    }
    disliked = set(
        db.scalars(
            select(LibraryTrackState.video_id).where(
                LibraryTrackState.video_id.in_(top_video_ids),
                LibraryTrackState.is_disliked.is_(True),
            )
        ).all()
    )

    gates = evaluate_snapshot(
        model,
        offline_mean_reward=offline_mean,
        current_mean_reward=current_mean,
        top_candidate_artists=[artists.get(video_id) for video_id in top_video_ids],
        top_candidate_disliked=[video_id in disliked for video_id in top_video_ids],
    )

    # Even a perfect snapshot waits for the clean baseline before serving.
    if not gates.passed:
        snapshot_status = "REJECTED"
    elif may_activate(status):
        snapshot_status = "ACTIVE"
    else:
        snapshot_status = "SHADOW"

    model_id = str(uuid.uuid4())
    snapshot = ModelSnapshot(
        model_id=model_id,
        algorithm=ALGORITHM,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        parameters_blob=model.to_bytes(),
        trained_through_time=now,
        training_counts_json={"samples": len(samples), "updates": model.update_count},
        metrics_json={
            "offlineMeanReward": offline_mean,
            "previousMeanReward": current_mean,
            "gateFailures": list(gates.failures),
        },
        status=snapshot_status,
        created_at=now,
        activated_at=now if snapshot_status == "ACTIVE" else None,
    )
    db.add(snapshot)

    if snapshot_status == "ACTIVE":
        for previous in db.scalars(
            select(ModelSnapshot).where(
                ModelSnapshot.status == "ACTIVE", ModelSnapshot.model_id != model_id
            )
        ):
            previous.status = "RETIRED"

    _prune_snapshots(db)
    db.flush()

    logger.info(
        "model training finished",
        extra={
            "operation": "model_train",
            "outcome": snapshot_status,
            "samples": len(samples),
            "offline_mean_reward": round(offline_mean, 4),
        },
    )
    return TrainingResult(
        model_id=model_id,
        status=snapshot_status,
        samples=len(samples),
        offline_mean_reward=offline_mean,
        gate_failures=gates.failures,
    )


def _prune_snapshots(db: Session) -> None:
    """Keep the active snapshot plus the last ten (docs/07 section 8)."""
    rows = db.scalars(
        select(ModelSnapshot)
        .where(ModelSnapshot.status != "ACTIVE")
        .order_by(ModelSnapshot.created_at.desc())
    ).all()
    for stale in rows[KEEP_SNAPSHOTS:]:
        db.delete(stale)


def load_active_model(db: Session) -> tuple[str, LinUcbModel] | None:
    snapshot = _latest_snapshot(db, "ACTIVE")
    if snapshot is None:
        return None
    return snapshot.model_id, LinUcbModel.from_bytes(
        snapshot.parameters_blob, snapshot.feature_schema_version
    )
