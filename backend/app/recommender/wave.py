"""Wave queue generation (docs/05 sections 3-11).

Opening the Wave never calls YouTube: candidates come from the local pool,
are filtered, scored by the current serving policy and finally sequenced by
the diversity reranker. Everything needed to reproduce the queue is stored
on the generation row.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ArtistAffinity,
    CandidateEdge,
    FeatureSnapshot,
    LibraryTrackState,
    PlaybackSession,
    QueueGeneration,
    QueueItem,
    Track,
    TrackAffinity,
    TrackArtist,
    utcnow,
)
from app.recommender.features import FEATURE_SCHEMA_VERSION, FeatureVector, build_features
from app.recommender.learning_state import Phase, learning_status
from app.recommender.linucb import LinUcbModel
from app.recommender.reranker import RerankCandidate, rerank
from app.recommender.rules import (
    SOURCE_STRENGTH,
    RuleFeatures,
    rule_score,
    rule_to_quality_expected,
)
from app.recommender.temperature import (
    exploration_alpha,
    familiar_quota,
    familiar_target_count,
)

RECENT_PLAY_EXCLUSION = 30
PLAYBACK_ERROR_COOLDOWN = dt.timedelta(hours=24)
REDISCOVERY_DAYS = 60
FATIGUE_WINDOW_DAYS = 7
STRONG_POSITIVE_REWARD = 0.4


@dataclass(frozen=True, slots=True)
class WaveRequest:
    temperature: int = 50
    mood: str = "ANY"
    length: int = 40
    exclude_video_ids: frozenset[str] = frozenset()
    random_seed: int | None = None


@dataclass(frozen=True, slots=True)
class WaveItem:
    position: int
    video_id: str
    title: str
    artists: tuple[str, ...]
    familiarity: str
    score: float
    quality_expected: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WaveResult:
    queue_id: str
    generation_id: str
    serving_policy: str
    serving_model_id: str | None
    shadow_model_id: str | None
    quality_score_source: str
    phase: str
    target_familiar_percent: int
    actual_familiar_percent: int
    relaxations: tuple[str, ...]
    items: tuple[WaveItem, ...]


def _recently_played(db: Session, limit: int = RECENT_PLAY_EXCLUSION) -> set[str]:
    rows = db.scalars(
        select(PlaybackSession.video_id).order_by(PlaybackSession.started_at.desc()).limit(limit)
    ).all()
    return set(rows)


def _error_cooldown(db: Session, now: dt.datetime) -> set[str]:
    """Tracks the iframe could not play in the last 24 hours."""
    since = now - PLAYBACK_ERROR_COOLDOWN
    rows = db.scalars(
        select(PlaybackSession.video_id).where(
            PlaybackSession.termination_reason == "player_error",
            PlaybackSession.started_at >= since,
        )
    ).all()
    return set(rows)


def _blocked(db: Session) -> set[str]:
    rows = db.scalars(
        select(LibraryTrackState.video_id).where(LibraryTrackState.is_disliked.is_(True))
    ).all()
    return set(rows)


def _liked(db: Session) -> set[str]:
    rows = db.scalars(
        select(LibraryTrackState.video_id).where(LibraryTrackState.is_liked.is_(True))
    ).all()
    return set(rows)


def _proven_playable(db: Session) -> set[str]:
    """Tracks the embedded player has actually played through before."""
    rows = db.scalars(
        select(PlaybackSession.video_id).where(PlaybackSession.played_seconds >= 5.0)
    ).all()
    return set(rows)


def _strong_positive(db: Session) -> set[str]:
    """Tracks with a stored strong positive local signal (docs/05 s.12)."""
    rows = db.scalars(
        select(PlaybackSession.video_id).where(
            PlaybackSession.qualified.is_(True),
            PlaybackSession.reward.is_not(None),
            PlaybackSession.reward >= STRONG_POSITIVE_REWARD,
        )
    ).all()
    return set(rows)


def _primary_artists(db: Session, video_ids: list[str]) -> dict[str, str]:
    if not video_ids:
        return {}
    rows = db.execute(
        select(TrackArtist.track_id, TrackArtist.artist_id).where(
            TrackArtist.track_id.in_(video_ids), TrackArtist.ordinal == 0
        )
    ).all()
    return {track_id: artist_id for track_id, artist_id in rows}


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


def _candidate_edges(db: Session, now: dt.datetime) -> dict[str, tuple[str, str, int]]:
    """video_id -> (source_type, seed_video_id, best rank), freshest first."""
    rows = db.scalars(
        select(CandidateEdge).where(CandidateEdge.expires_at > now).order_by(CandidateEdge.rank)
    ).all()
    best: dict[str, tuple[str, str, int]] = {}
    for row in rows:
        current = best.get(row.candidate_video_id)
        if current is None or row.rank < current[2]:
            best[row.candidate_video_id] = (row.source_type, row.seed_video_id, row.rank)
    return best


def _build_features(
    *,
    video_id: str,
    source_type: str,
    seed_affinity: float,
    artist_affinity: float,
    affinity: TrackAffinity | None,
    temperature: int,
    now: dt.datetime,
) -> RuleFeatures:
    strength = SOURCE_STRENGTH.get(source_type, 0.5)

    rediscovery = 0.0
    fatigue = 0.0
    recent_skip = 0.0
    novelty = 1.0

    if affinity is not None:
        if affinity.plays_all > 0:
            novelty = 0.0
        if affinity.last_played_at is not None:
            days = (now - affinity.last_played_at).days
            if days >= REDISCOVERY_DAYS:
                rediscovery = min(1.0, days / 365)
        # Recent repetition tires the ear.
        fatigue = min(0.30, 0.10 * affinity.plays_7d)
        if affinity.last_skipped_at is not None:
            skipped_days = (now - affinity.last_skipped_at).days
            if skipped_days <= FATIGUE_WINDOW_DAYS:
                recent_skip = 0.25

    # High temperature values novelty, low temperature values the familiar.
    novelty_weighted = novelty * (temperature / 100)

    return RuleFeatures(
        source_strength=strength,
        seed_affinity=seed_affinity,
        artist_affinity=artist_affinity,
        rediscovery=rediscovery,
        novelty=novelty_weighted,
        fatigue_penalty=fatigue,
        recent_skip_penalty=recent_skip,
    )


def generate_wave(db: Session, request: WaveRequest) -> WaveResult:
    from app.jobs.model_train import load_active_model

    now = utcnow()
    seed = request.random_seed if request.random_seed is not None else random.randrange(2**31)
    rng = random.Random(seed)

    status = learning_status(db)
    serving_model: LinUcbModel | None = None
    serving_model_id: str | None = None
    if status.phase is Phase.ACTIVE:
        loaded = load_active_model(db)
        if loaded is not None:
            serving_model_id, serving_model = loaded
    alpha = exploration_alpha(request.temperature)
    recent_reward = _recent_mean_reward(db)

    blocked = _blocked(db)
    liked = _liked(db)
    strong_positive = _strong_positive(db)
    proven = _proven_playable(db)
    recent = _recently_played(db)
    error_cooldown = _error_cooldown(db, now)
    edges = _candidate_edges(db, now)

    # Pool: liked tracks plus everything reachable through fresh edges.
    pool_ids = (liked | strong_positive | set(edges)) - blocked - error_cooldown
    pool_ids -= request.exclude_video_ids
    pool_ids -= recent

    tracks = {
        row.video_id: row
        for row in db.scalars(
            select(Track).where(
                Track.video_id.in_(pool_ids),
                Track.is_playable.is_(True),
                Track.remote_deleted_at.is_(None),
            )
        )
    }
    affinities = {
        row.video_id: row
        for row in db.scalars(select(TrackAffinity).where(TrackAffinity.video_id.in_(tracks)))
    }
    primary_artists = _primary_artists(db, list(tracks))
    artist_affinity_rows = {
        row.artist_id: row
        for row in db.scalars(
            select(ArtistAffinity).where(
                ArtistAffinity.artist_id.in_(set(primary_artists.values()))
            )
        )
    }

    candidates: list[RerankCandidate] = []
    feature_vectors: dict[str, FeatureVector] = {}
    for video_id, track in tracks.items():
        source_type, seed_video_id, rank = edges.get(video_id, ("LIKED", None, 1))
        if video_id in liked:
            source_type = "LIKED"

        artist_id = primary_artists.get(video_id)
        artist_row = artist_affinity_rows.get(artist_id) if artist_id else None
        artist_affinity = min(1.0, max(0.0, artist_row.decayed_reward)) if artist_row else 0.0
        seed_affinity = 1.0 / (1.0 + max(0, rank - 1) * 0.1)

        features = _build_features(
            video_id=video_id,
            source_type=source_type,
            seed_affinity=seed_affinity,
            artist_affinity=artist_affinity,
            affinity=affinities.get(video_id),
            temperature=request.temperature,
            now=now,
        )
        rule_value = rule_score(features)
        affinity_row = affinities.get(video_id)
        artist_row_for_features = artist_affinity_rows.get(artist_id) if artist_id else None
        vector = build_features(
            is_liked=video_id in liked,
            artist_decayed_reward=(
                artist_row_for_features.decayed_reward if artist_row_for_features else 0.0
            ),
            seed_mean_reward=affinity_row.decayed_reward if affinity_row else 0.0,
            distinct_seed_count=1,
            best_source_rank=rank,
            plays_all=affinity_row.plays_all if affinity_row else 0,
            last_played_at=affinity_row.last_played_at if affinity_row else None,
            plays_7d=affinity_row.plays_7d if affinity_row else 0,
            artist_plays_7d=(artist_row_for_features.plays_7d if artist_row_for_features else 0),
            skipped_recently=bool(affinity_row and affinity_row.last_skipped_at is not None),
            temperature=request.temperature,
            recent_session_reward=recent_reward,
            has_duration=track.metadata_duration_seconds is not None,
            observation_count=affinity_row.plays_all if affinity_row else 0,
            now=now,
        )
        feature_vectors[video_id] = vector

        if serving_model is not None:
            x = vector.to_array()
            quality = serving_model.expected(x)
            score = serving_model.ucb_score(x, alpha)
        else:
            score = rule_value
            quality = rule_to_quality_expected(rule_value)

        familiar = video_id in liked or video_id in strong_positive
        reasons = _reason_codes(
            familiar=familiar,
            liked=video_id in liked,
            source_type=source_type,
            affinity=affinities.get(video_id),
            now=now,
        )

        # A small deterministic jitter keeps equal scores from always tying
        # the same way while staying reproducible for a fixed seed.
        jitter = rng.uniform(0.0, 0.01) * exploration_alpha(request.temperature)

        candidates.append(
            RerankCandidate(
                video_id=video_id,
                score=score + jitter,
                artist_id=artist_id,
                album_id=track.album_id,
                seed_video_id=seed_video_id,
                source_type=source_type,
                familiar=familiar,
                quality_expected=quality,
                proven_playable=video_id in proven,
                reason_codes=reasons,
            )
        )

    candidates.sort(key=lambda item: item.score, reverse=True)
    familiar_target = familiar_target_count(request.temperature, request.length)
    reranked = rerank(candidates, request.length, familiar_target)

    relaxations = list(reranked.relaxations)
    available_familiar = sum(1 for item in candidates if item.familiar)
    if reranked.actual_familiar_count < familiar_target and available_familiar < familiar_target:
        relaxations.append("FAMILIAR_POOL_WIDENED")

    queue_id = str(uuid.uuid4())
    generation_id = str(uuid.uuid4())
    actual_percent = (
        round(100 * reranked.actual_familiar_count / len(reranked.items)) if reranked.items else 0
    )

    generation = QueueGeneration(
        generation_id=generation_id,
        queue_id=queue_id,
        temperature=request.temperature,
        mood=request.mood,
        serving_policy=status.serving_policy,
        serving_model_id=serving_model_id,
        shadow_model_id=status.shadow_model_id,
        quality_score_source=("LINUCB_EXPECTED" if serving_model is not None else "RULE_MAPPED"),
        random_seed=seed,
        pool_watermark=now,
        target_familiar_percent=familiar_quota(request.temperature),
        actual_familiar_percent=actual_percent,
        relaxations_json={"codes": relaxations},
    )
    db.add(generation)

    names = _artist_names(db, [item.video_id for item in reranked.items])
    items: list[WaveItem] = []
    for position, item in enumerate(reranked.items, start=1):
        db.add(
            QueueItem(
                generation_id=generation_id,
                queue_id=queue_id,
                position=position,
                video_id=item.video_id,
                score=item.score,
                quality_expected=item.quality_expected,
                familiarity="FAMILIAR" if item.familiar else "DISCOVERY",
                reason_codes_json={"codes": list(item.reason_codes)},
            )
        )
        selected_vector = feature_vectors.get(item.video_id)
        if selected_vector is not None:
            db.add(
                FeatureSnapshot(
                    generation_id=generation_id,
                    video_id=item.video_id,
                    feature_schema_version=FEATURE_SCHEMA_VERSION,
                    features_json=selected_vector.to_dict(),
                    created_at=now,
                )
            )
        track = tracks[item.video_id]
        items.append(
            WaveItem(
                position=position,
                video_id=item.video_id,
                title=track.title,
                artists=tuple(names.get(item.video_id, [])),
                familiarity="FAMILIAR" if item.familiar else "DISCOVERY",
                score=item.score,
                quality_expected=item.quality_expected,
                reason_codes=item.reason_codes,
            )
        )
    db.flush()

    return WaveResult(
        queue_id=queue_id,
        generation_id=generation_id,
        serving_policy=status.serving_policy,
        serving_model_id=serving_model_id,
        shadow_model_id=status.shadow_model_id,
        quality_score_source=("LINUCB_EXPECTED" if serving_model is not None else "RULE_MAPPED"),
        phase=status.phase.value,
        target_familiar_percent=familiar_quota(request.temperature),
        actual_familiar_percent=actual_percent,
        relaxations=tuple(relaxations),
        items=tuple(items),
    )


def _reason_codes(
    *,
    familiar: bool,
    liked: bool,
    source_type: str,
    affinity: TrackAffinity | None,
    now: dt.datetime,
) -> tuple[str, ...]:
    """Deterministic explanations, never generated text (docs/06 section 4)."""
    codes: list[str] = []
    if liked:
        codes.append("LIKED_TRACK")
    elif familiar:
        codes.append("STRONG_LOCAL_SIGNAL")

    if source_type == "RELATED":
        codes.append("RELATED_TO_POSITIVE_SEED")
    elif source_type == "RADIO":
        codes.append("RADIO_SOURCE")
    elif source_type == "MOOD":
        codes.append("MOOD_MATCH")

    if affinity is None or affinity.plays_all == 0:
        codes.append("NEVER_PLAYED")
    elif affinity.last_played_at is not None:
        days = (now - affinity.last_played_at).days
        if days >= REDISCOVERY_DAYS:
            codes.append("REDISCOVERY")
        elif days >= 14:
            codes.append("ARTIST_NOT_RECENT")

    return tuple(codes[:3])


def queue_count(db: Session, queue_id: str) -> int:
    return (
        db.scalar(select(func.count()).select_from(QueueItem).where(QueueItem.queue_id == queue_id))
        or 0
    )


def _recent_mean_reward(db: Session, limit: int = 5) -> float:
    """Mean reward of the last few qualified sessions (context feature)."""
    rows = db.scalars(
        select(PlaybackSession.reward)
        .where(PlaybackSession.qualified.is_(True), PlaybackSession.reward.is_not(None))
        .order_by(PlaybackSession.started_at.desc())
        .limit(limit)
    ).all()
    rewards = [float(value) for value in rows if value is not None]
    return sum(rewards) / len(rewards) if rewards else 0.0
