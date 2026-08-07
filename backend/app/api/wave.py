"""Wave and queue endpoints (docs/08 section 4)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import errors
from app.persistence.database import get_session
from app.persistence.models import QueueGeneration, QueueItem, Track, TrackArtist, utcnow
from app.recommender.wave import WaveRequest, WaveResult, generate_wave

router = APIRouter(prefix="/api/v1", tags=["wave"])

MOODS = ("ANY", "FOCUS", "ENERGY", "CALM", "BACKGROUND", "REDISCOVER")
EXTEND_LENGTH = 20


class CreateWaveRequest(BaseModel):
    temperature: int = Field(default=50, ge=0, le=100)
    mood: Literal["ANY", "FOCUS", "ENERGY", "CALM", "BACKGROUND", "REDISCOVER"] = "ANY"
    length: int = Field(default=40, ge=1, le=200)
    excludeVideoIds: list[str] = Field(default_factory=list)
    randomSeed: int | None = None


class PatchWaveRequest(BaseModel):
    temperature: int | None = Field(default=None, ge=0, le=100)
    mood: Literal["ANY", "FOCUS", "ENERGY", "CALM", "BACKGROUND", "REDISCOVER"] | None = None


def serialize(result: WaveResult) -> dict[str, Any]:
    return {
        "queueId": result.queue_id,
        "generationId": result.generation_id,
        "ranking": {
            "phase": result.phase,
            "servingPolicy": result.serving_policy,
            "servingModelId": result.serving_model_id,
            "shadowModelId": result.shadow_model_id,
            "qualityScoreSource": result.quality_score_source,
        },
        "mix": {
            "targetFamiliarPercent": result.target_familiar_percent,
            "actualFamiliarPercent": result.actual_familiar_percent,
            "actualDiscoveryPercent": 100 - result.actual_familiar_percent,
        },
        "freshness": {
            "poolSize": result.pool_size,
            "overlapPreviousPercent": result.overlap_previous_percent,
        },
        "relaxations": list(result.relaxations),
        "items": [
            {
                "position": item.position,
                "track": {
                    "videoId": item.video_id,
                    "title": item.title,
                    "artists": list(item.artists),
                },
                "reasonCodes": list(item.reason_codes),
                "familiarity": item.familiarity,
            }
            for item in result.items
        ],
    }


@router.post("/waves")
def create_wave(body: CreateWaveRequest, db: Session = Depends(get_session)) -> dict[str, Any]:
    result = generate_wave(
        db,
        WaveRequest(
            temperature=body.temperature,
            mood=body.mood,
            length=body.length,
            exclude_video_ids=frozenset(body.excludeVideoIds),
            random_seed=body.randomSeed,
        ),
    )
    if not result.items:
        raise errors.ValidationFailed(
            "no eligible candidates yet — sync the library and refresh candidates first",
            reasonCode="EMPTY_POOL",
        )
    return serialize(result)


@router.get("/waves/{queue_id}")
def get_wave(queue_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Restore a queue after a reload."""
    generation = db.scalar(
        select(QueueGeneration)
        .where(QueueGeneration.queue_id == queue_id)
        .order_by(QueueGeneration.created_at.desc())
        .limit(1)
    )
    if generation is None:
        raise errors.ValidationFailed("unknown queue")

    items = db.scalars(
        select(QueueItem).where(QueueItem.queue_id == queue_id).order_by(QueueItem.position)
    ).all()
    video_ids = [item.video_id for item in items]
    tracks = {
        row.video_id: row for row in db.scalars(select(Track).where(Track.video_id.in_(video_ids)))
    }
    names = _artist_names(db, video_ids)

    return {
        "queueId": queue_id,
        "generationId": generation.generation_id,
        "ranking": {
            # Restored from the stored generation: a shadow model was watching
            # but not serving, and the label must not upgrade it to BASELINE.
            "phase": (
                "ACTIVE"
                if generation.serving_model_id is not None
                else "SHADOW"
                if generation.shadow_model_id is not None
                else "BASELINE"
            ),
            "servingPolicy": generation.serving_policy,
            "servingModelId": generation.serving_model_id,
            "shadowModelId": generation.shadow_model_id,
            "qualityScoreSource": generation.quality_score_source,
        },
        "mix": {
            "targetFamiliarPercent": generation.target_familiar_percent,
            "actualFamiliarPercent": generation.actual_familiar_percent,
            "actualDiscoveryPercent": 100 - generation.actual_familiar_percent,
        },
        "relaxations": generation.relaxations_json.get("codes", []),
        "items": [
            {
                "position": item.position,
                "track": {
                    "videoId": item.video_id,
                    "title": tracks[item.video_id].title if item.video_id in tracks else "",
                    "artists": names.get(item.video_id, []),
                },
                "reasonCodes": item.reason_codes_json.get("codes", []),
                "familiarity": item.familiarity,
            }
            for item in items
        ],
    }


@router.post("/waves/{queue_id}/extend")
def extend_wave(queue_id: str, db: Session = Depends(get_session)) -> dict[str, Any]:
    """Twenty more locally ranked items; no external call."""
    generation = db.scalar(
        select(QueueGeneration)
        .where(QueueGeneration.queue_id == queue_id)
        .order_by(QueueGeneration.created_at.desc())
        .limit(1)
    )
    if generation is None:
        raise errors.ValidationFailed("unknown queue")

    played = set(db.scalars(select(QueueItem.video_id).where(QueueItem.queue_id == queue_id)).all())
    result = generate_wave(
        db,
        WaveRequest(
            temperature=generation.temperature,
            mood=generation.mood,
            length=EXTEND_LENGTH,
            exclude_video_ids=frozenset(played),
        ),
    )
    return serialize(result)


@router.patch("/waves/{queue_id}")
def patch_wave(
    queue_id: str, body: PatchWaveRequest, db: Session = Depends(get_session)
) -> dict[str, Any]:
    """A new temperature/mood applies to the unplayed tail only."""
    generation = db.scalar(
        select(QueueGeneration)
        .where(QueueGeneration.queue_id == queue_id)
        .order_by(QueueGeneration.created_at.desc())
        .limit(1)
    )
    if generation is None:
        raise errors.ValidationFailed("unknown queue")

    played = set(
        db.scalars(
            select(QueueItem.video_id).where(
                QueueItem.queue_id == queue_id, QueueItem.played_at.is_not(None)
            )
        ).all()
    )
    result = generate_wave(
        db,
        WaveRequest(
            temperature=body.temperature
            if body.temperature is not None
            else generation.temperature,
            mood=body.mood or generation.mood,
            length=40,
            exclude_video_ids=frozenset(played),
        ),
    )
    return serialize(result)


@router.post("/waves/{queue_id}/exclude")
def exclude_candidate(
    queue_id: str, body: dict[str, str], db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Drop one candidate from this queue without a global dislike."""
    video_id = body.get("videoId")
    if not video_id:
        raise errors.ValidationFailed("videoId is required")

    removed = (
        db.query(QueueItem)
        .filter(
            QueueItem.queue_id == queue_id,
            QueueItem.video_id == video_id,
            QueueItem.played_at.is_(None),
        )
        .delete(synchronize_session=False)
    )
    return {"queueId": queue_id, "videoId": video_id, "removed": int(removed)}


@router.post("/waves/{queue_id}/played")
def mark_played(
    queue_id: str, body: dict[str, str], db: Session = Depends(get_session)
) -> dict[str, Any]:
    video_id = body.get("videoId")
    if not video_id:
        raise errors.ValidationFailed("videoId is required")
    item = db.scalar(
        select(QueueItem).where(QueueItem.queue_id == queue_id, QueueItem.video_id == video_id)
    )
    if item is None:
        raise errors.ValidationFailed("track is not part of this queue")
    item.played_at = utcnow()
    return {"queueId": queue_id, "videoId": video_id}


def _artist_names(db: Session, video_ids: list[str]) -> dict[str, list[str]]:
    from app.persistence.models import Artist

    if not video_ids:
        return {}
    rows = db.execute(
        select(TrackArtist.track_id, Artist.name)
        .join(Artist, Artist.artist_id == TrackArtist.artist_id)
        .where(TrackArtist.track_id.in_(video_ids))
        .order_by(TrackArtist.track_id, TrackArtist.ordinal)
    ).all()
    grouped: dict[str, list[str]] = {}
    for track_id, name in rows:
        grouped.setdefault(track_id, []).append(name)
    return grouped
