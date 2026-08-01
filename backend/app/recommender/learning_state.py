"""Bootstrap thresholds and activation phases (docs/05 section 7, BR-009).

Sessions 1-100 are always served by the frozen rule ranker so the baseline
stays clean. From session 40, once all four data thresholds are met, LinUCB
trains in SHADOW: it scores and is measured, but never orders the queue.
ACTIVE is only possible after the clean baseline closes and the safety gates
pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import ModelSnapshot, PlaybackSession

BOOTSTRAP_MIN_SESSIONS = 40
BOOTSTRAP_MIN_TRACKS = 20
BOOTSTRAP_MIN_POSITIVE = 12
BOOTSTRAP_MIN_NEGATIVE = 8
CLEAN_BASELINE_SESSIONS = 100

POSITIVE_REWARD_THRESHOLD = 0.0
RETRAIN_MIN_NEW_SESSIONS = 15


class Phase(StrEnum):
    BASELINE = "BASELINE"
    SHADOW = "SHADOW"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True)
class LearningStatus:
    qualified_sessions: int
    distinct_tracks: int
    positive_sessions: int
    negative_sessions: int
    bootstrap_ready: bool
    baseline_complete: bool
    phase: Phase
    serving_policy: str
    serving_model_id: str | None
    shadow_model_id: str | None

    @property
    def missing(self) -> dict[str, int]:
        return {
            "sessions": max(0, BOOTSTRAP_MIN_SESSIONS - self.qualified_sessions),
            "tracks": max(0, BOOTSTRAP_MIN_TRACKS - self.distinct_tracks),
            "positive": max(0, BOOTSTRAP_MIN_POSITIVE - self.positive_sessions),
            "negative": max(0, BOOTSTRAP_MIN_NEGATIVE - self.negative_sessions),
        }


def _counts(db: Session) -> tuple[int, int, int, int]:
    qualified = select(PlaybackSession).where(
        PlaybackSession.qualified.is_(True), PlaybackSession.source == "TUNER"
    )
    total = db.scalar(select(func.count()).select_from(qualified.subquery())) or 0
    distinct = (
        db.scalar(
            select(func.count(func.distinct(PlaybackSession.video_id))).where(
                PlaybackSession.qualified.is_(True), PlaybackSession.source == "TUNER"
            )
        )
        or 0
    )
    positive = (
        db.scalar(
            select(func.count()).select_from(
                qualified.where(PlaybackSession.reward > POSITIVE_REWARD_THRESHOLD).subquery()
            )
        )
        or 0
    )
    negative = (
        db.scalar(
            select(func.count()).select_from(
                qualified.where(PlaybackSession.reward < POSITIVE_REWARD_THRESHOLD).subquery()
            )
        )
        or 0
    )
    return total, distinct, positive, negative


def learning_status(db: Session) -> LearningStatus:
    total, distinct, positive, negative = _counts(db)

    bootstrap_ready = (
        total >= BOOTSTRAP_MIN_SESSIONS
        and distinct >= BOOTSTRAP_MIN_TRACKS
        and positive >= BOOTSTRAP_MIN_POSITIVE
        and negative >= BOOTSTRAP_MIN_NEGATIVE
    )
    baseline_complete = total >= CLEAN_BASELINE_SESSIONS

    active = db.scalar(select(ModelSnapshot).where(ModelSnapshot.status == "ACTIVE"))
    shadow = db.scalar(
        select(ModelSnapshot)
        .where(ModelSnapshot.status == "SHADOW")
        .order_by(ModelSnapshot.created_at.desc())
        .limit(1)
    )

    if active is not None and baseline_complete:
        phase = Phase.ACTIVE
    elif shadow is not None and bootstrap_ready:
        phase = Phase.SHADOW
    else:
        phase = Phase.BASELINE

    return LearningStatus(
        qualified_sessions=total,
        distinct_tracks=distinct,
        positive_sessions=positive,
        negative_sessions=negative,
        bootstrap_ready=bootstrap_ready,
        baseline_complete=baseline_complete,
        phase=phase,
        serving_policy="linucb-v1" if phase is Phase.ACTIVE else "rule-score-v1",
        serving_model_id=active.model_id if phase is Phase.ACTIVE and active else None,
        shadow_model_id=shadow.model_id if phase is not Phase.ACTIVE and shadow else None,
    )


def may_activate(status: LearningStatus) -> bool:
    """A snapshot may only serve once the clean baseline is closed."""
    return status.bootstrap_ready and status.baseline_complete


def progress_label(status: LearningStatus) -> str:
    """UI text that never over-promises (docs/06 sections 7 and 11)."""
    if not status.bootstrap_ready:
        missing = status.missing
        if missing["sessions"] > 0:
            return f"Collecting signal {status.qualified_sessions}/{BOOTSTRAP_MIN_SESSIONS}"
        parts = [f"{status.qualified_sessions} sessions"]
        if missing["negative"] > 0:
            parts.append(f"{status.negative_sessions}/{BOOTSTRAP_MIN_NEGATIVE} negative signals")
        if missing["positive"] > 0:
            parts.append(f"{status.positive_sessions}/{BOOTSTRAP_MIN_POSITIVE} positive signals")
        if missing["tracks"] > 0:
            parts.append(f"{status.distinct_tracks}/{BOOTSTRAP_MIN_TRACKS} distinct tracks")
        return " · ".join(parts)
    if status.phase is Phase.ACTIVE:
        return "Model active"
    return (
        f"Baseline {min(status.qualified_sessions, CLEAN_BASELINE_SESSIONS)}"
        f"/{CLEAN_BASELINE_SESSIONS} · model in shadow"
    )
