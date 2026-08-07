"""Wave queue generation (docs/05 sections 3-11).

Opening the Wave never calls YouTube: candidates come from the local graph,
are filtered, scored by the current serving policy and finally sequenced by
the diversity reranker. Everything needed to reproduce the queue is stored
on the generation row.

Freshness is a first-class requirement, not a side effect of ranking. Three
mechanisms keep consecutive waves from repeating each other:

* the pool is the whole accumulated graph rather than a seven-day window;
* tracks from recent generations are excluded outright while the pool can
  afford it, and penalised when it cannot;
* selection is stochastic, so even an unchanged pool yields a different
  ordering every time.
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
from app.recommender.graph import CandidateSupport, build_support, hop_discount
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

# The recency window scales with the pool instead of being a flat 30: with a
# few hundred candidates a fixed window hides almost nothing.
RECENT_PLAY_MIN = 30
RECENT_PLAY_MAX = 400
RECENT_PLAY_POOL_SHARE = 0.35

# Familiar tracks are held to a much shorter window. Hearing a favourite
# again next week is the point of a familiar quota; the wide window is there
# to stop *discovery* from recycling, and applying it to likes simply empties
# the familiar side of the wave. It also scales down with the number of
# favourites that can actually be played — a listener whose likes are mostly
# blocked from embedding has very few to rotate through.
FAMILIAR_RECENT_WINDOW = 15
FAMILIAR_RECENT_SHARE = 3

# A wave never consumes more than this share of the familiar tracks available
# to it. Take them all and the next wave has nothing left to differ by, which
# is how the quota alone used to produce a 45% repeat rate. A cold wave is an
# explicit request for the familiar, so it may reuse a little more of the
# pool; a hot one leaves more in reserve.
ROTATION_SHARE_COLD = 0.75
ROTATION_SHARE_HOT = 0.50


def rotation_share(temperature: int) -> float:
    position = max(0, min(100, temperature)) / 100
    return ROTATION_SHARE_COLD + position * (ROTATION_SHARE_HOT - ROTATION_SHARE_COLD)


# How many past generations are remembered when avoiding repeats, and how
# hard each one is penalised. Index 0 is the wave just before this one.
WAVE_HISTORY_DEPTH = 4
HISTORY_PENALTY: tuple[float, ...] = (0.90, 0.45, 0.22, 0.10)
# Favourites are a small set — a handful of likes cannot fill four waves
# without repeating — so the same penalty would empty the familiar quota
# after a few restarts. Repeating a liked track is not the complaint.
FAMILIAR_HISTORY_PENALTY: tuple[float, ...] = (0.20, 0.10, 0.05, 0.02)
# Avoiding repeated *tracks* is not enough: with a large pool the ranker still
# converged on the same eighty artists, which is what "every wave sounds the
# same" actually means. Artists heard in recent waves step back so the rest of
# the graph gets a turn. Likes are exempt — they are the point of the quota.
ARTIST_HISTORY_PENALTY: tuple[float, ...] = (0.35, 0.22, 0.12, 0.06)

PLAYBACK_ERROR_COOLDOWN = dt.timedelta(hours=24)
REDISCOVERY_DAYS = 60
FATIGUE_WINDOW_DAYS = 7
STRONG_POSITIVE_REWARD = 0.4

# When the Rediscover pool is too small to fill the wave outright, matching
# tracks are boosted instead of filtered (docs/05 section 10). Sized like the
# artist history penalty so it can actually move a candidate up the ranking.
REDISCOVER_BOOST = 0.35

# Moods that would need YouTube Music mood-playlist membership, which the
# graph does not ingest yet: they cannot narrow the pool, and the response
# must say so instead of pretending to filter (docs/05 section 10).
UNSUPPORTED_MOODS = frozenset({"FOCUS", "ENERGY", "CALM", "BACKGROUND"})

# How much agreement between several liked seeds may lift a candidate above
# its plain source rank.
SUPPORT_BONUS = 0.5

# Fatigue and skip penalties are scaled down this much for explicit likes.
LIKED_PENALTY_SCALE = 0.35

# A track you finished at least once and still feel warm about counts as
# familiar even without a like — otherwise the familiar quota keeps recycling
# the same handful of likes.
WARM_REWARD_FLOOR = 0.15

# Escalating quarantine for tracks you keep skipping (docs/05 section 4).
SKIP_QUARANTINE_SECOND = dt.timedelta(days=7)
SKIP_QUARANTINE_THIRD = dt.timedelta(days=30)


@dataclass(frozen=True, slots=True)
class WaveRequest:
    temperature: int = 50
    mood: str = "ANY"
    length: int = 40
    exclude_video_ids: frozenset[str] = frozenset()
    random_seed: int | None = None
    # A published playlist is a snapshot, not a stream: it is read whenever
    # the listener opens it, so it must be the best selection available
    # rather than one that differs from yesterday's. The freshness machinery
    # — recency windows, wave history, the rotation cap — is skipped for it,
    # otherwise the very tracks the familiar quota needs are held back.
    for_publishing: bool = False


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
    pool_size: int = 0
    overlap_previous_percent: int = 0


def _recently_played(db: Session, limit: int) -> list[str]:
    if limit <= 0:
        return []
    return list(
        db.scalars(
            select(PlaybackSession.video_id)
            .order_by(PlaybackSession.started_at.desc())
            .limit(limit)
        ).all()
    )


def recency_window(pool_size: int) -> int:
    """How many recently played tracks to hide, given how much material exists.

    A flat thirty was far too small once the graph started growing, so the
    window follows the pool. It never exceeds half of it: hearing something
    again is annoying, but an empty wave is worse.
    """
    desired = min(RECENT_PLAY_MAX, max(RECENT_PLAY_MIN, int(pool_size * RECENT_PLAY_POOL_SHARE)))
    return min(desired, pool_size // 2)


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


def skip_quarantine(db: Session, now: dt.datetime, protected: set[str] | None = None) -> set[str]:
    """Tracks you have skipped often enough to deserve a rest.

    Two skips buy a week off, three or more a month. A track you have also
    finished at least once is given more rope: skipping it may just have been
    the wrong moment. Liked tracks are never quarantined — an explicit like
    outranks any number of "not right now" skips.
    """
    protected = protected or set()
    quarantined: set[str] = set()
    rows = db.scalars(select(TrackAffinity).where(TrackAffinity.skips > 0))
    for row in rows:
        if row.last_skipped_at is None or row.video_id in protected:
            continue
        age = now - row.last_skipped_at
        threshold = row.skips - (1 if row.completions > 0 else 0)
        if (threshold >= 3 and age < SKIP_QUARANTINE_THIRD) or (
            threshold == 2 and age < SKIP_QUARANTINE_SECOND
        ):
            quarantined.add(row.video_id)
    return quarantined


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


def _warm(db: Session) -> set[str]:
    """Played before and still positive on the decayed average."""
    rows = db.scalars(
        select(TrackAffinity.video_id).where(
            TrackAffinity.plays_all > 0,
            TrackAffinity.decayed_reward >= WARM_REWARD_FLOOR,
        )
    ).all()
    return set(rows)


def recent_generations(db: Session, depth: int = WAVE_HISTORY_DEPTH) -> list[set[str]]:
    """Video ids of the last few waves, newest first."""
    generation_ids = list(
        db.scalars(
            select(QueueGeneration.generation_id)
            .order_by(QueueGeneration.created_at.desc(), QueueGeneration.generation_id.desc())
            .limit(depth)
        ).all()
    )
    if not generation_ids:
        return []
    grouped: dict[str, set[str]] = {generation_id: set() for generation_id in generation_ids}
    rows = db.execute(
        select(QueueItem.generation_id, QueueItem.video_id).where(
            QueueItem.generation_id.in_(generation_ids)
        )
    ).all()
    for generation_id, video_id in rows:
        grouped[generation_id].add(video_id)
    return [grouped[generation_id] for generation_id in generation_ids]


def recent_generation_artists(db: Session, history: list[set[str]]) -> dict[str, int]:
    """Primary artist -> how many waves ago it was last served."""
    if not history:
        return {}
    everything = sorted({video_id for generation in history for video_id in generation})
    artists = _primary_artists(db, everything)
    position: dict[str, int] = {}
    for index, generation in enumerate(history):
        for video_id in generation:
            artist = artists.get(video_id)
            if artist is not None:
                position.setdefault(artist, index)
    return position


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


def _build_features(
    *,
    source_type: str,
    seed_affinity: float,
    artist_affinity: float,
    support: CandidateSupport | None,
    affinity: TrackAffinity | None,
    temperature: int,
    now: dt.datetime,
    liked: bool = False,
) -> RuleFeatures:
    strength = SOURCE_STRENGTH.get(source_type, 0.5)
    if support is not None:
        # Distance from a track you like matters as much as which endpoint
        # produced the edge.
        strength *= hop_discount(support.min_hop)

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
            # Every skip beyond the first keeps counting even after the week.
            recent_skip += min(0.25, 0.08 * max(0, affinity.skips - 1))
        if liked:
            # A track you deliberately liked is one you asked to hear often,
            # and skipping it usually means "not right now" rather than "not
            # this track". At full strength these two penalties pushed most
            # favourites below the publishing quality floor.
            fatigue *= LIKED_PENALTY_SCALE
            recent_skip *= LIKED_PENALTY_SCALE

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


@dataclass(frozen=True, slots=True)
class _Eligibility:
    ids: set[str]
    history_index: dict[str, int]
    relaxations: tuple[str, ...]
    recency_window: int


def _apply_freshness(
    *,
    pool: set[str],
    familiar_pool: set[str],
    recent: list[str],
    history: list[set[str]],
    length: int,
    familiar_target: int,
) -> _Eligibility:
    """Decide what may appear, keeping the wave as fresh as the pool allows.

    Discovery repeats are what grate, so the previous waves are removed from
    the discovery side first and from the familiar side only while the quota
    can still be met.
    """
    relaxations: list[str] = []
    recent_set = set(recent)
    familiar_window = min(FAMILIAR_RECENT_WINDOW, len(familiar_pool) // FAMILIAR_RECENT_SHARE)
    recent_familiar = set(recent[:familiar_window])

    # Recency is a hard filter, but a two-speed one: wide for discovery,
    # narrow for the familiar side.
    discovery_pool = pool - familiar_pool
    familiar_available = (pool & familiar_pool) - recent_familiar
    discovery_available = discovery_pool - recent_set
    reachable = len(familiar_available) + len(discovery_available)

    history_index: dict[str, int] = {}
    for index, generation in enumerate(history):
        for video_id in generation:
            history_index.setdefault(video_id, index)

    excluded: set[str] = set()
    if history:
        previous = history[0]
        discovery_target = max(0, length - familiar_target)
        fresh_discovery = discovery_available - previous
        if len(fresh_discovery) >= discovery_target:
            excluded |= previous & discovery_available
        else:
            relaxations.append("DISCOVERY_REPEAT_ALLOWED")

        fresh_familiar = familiar_available - previous
        if len(fresh_familiar) >= familiar_target:
            excluded |= previous & familiar_available
        else:
            relaxations.append("FAMILIAR_REPEAT_ALLOWED")

    eligible = (familiar_available | discovery_available) - excluded
    # Only undo the exclusion if it actually cost the wave tracks it could
    # otherwise have had — a small pool is a reason for a short wave, not for
    # replaying the previous one.
    if len(eligible) < min(length, reachable):
        eligible = familiar_available | discovery_available
        if "DISCOVERY_REPEAT_ALLOWED" not in relaxations:
            relaxations.append("DISCOVERY_REPEAT_ALLOWED")

    return _Eligibility(
        ids=eligible,
        history_index=history_index,
        relaxations=tuple(dict.fromkeys(relaxations)),
        recency_window=len(recent_set),
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
    warm = _warm(db)
    proven = _proven_playable(db)
    error_cooldown = _error_cooldown(db, now)
    quarantined = skip_quarantine(db, now, protected=liked)
    support_by_id = build_support(db, now=now)

    # The pool is the whole graph: edges are knowledge, not a cache.
    pool_ids = (liked | strong_positive | warm | set(support_by_id)) - blocked
    pool_ids -= error_cooldown
    pool_ids -= quarantined
    pool_ids -= request.exclude_video_ids

    playable = {
        row.video_id: row
        for row in db.scalars(
            select(Track).where(
                Track.video_id.in_(pool_ids),
                Track.is_playable.is_(True),
                Track.remote_deleted_at.is_(None),
            )
        )
    }
    pool_size = len(playable)

    familiar_pool = (liked | strong_positive | warm) & set(playable)
    familiar_target = familiar_target_count(request.temperature, request.length)
    window = 0 if request.for_publishing else recency_window(pool_size)
    history = [] if request.for_publishing else recent_generations(db)
    freshness = _apply_freshness(
        pool=set(playable),
        familiar_pool=familiar_pool,
        recent=_recently_played(db, window),
        history=history,
        length=request.length,
        familiar_target=familiar_target,
    )

    artist_history = {} if request.for_publishing else recent_generation_artists(db, history)

    # Mood narrows the pool where local data allows it; a context that cannot
    # change the selection declares CONTEXT_WIDENED rather than staying quiet.
    eligible_ids = set(freshness.ids)
    rediscover_boost_ids: set[str] = set()
    mood_relaxations: list[str] = []
    if request.mood == "REDISCOVER":
        cutoff = now - dt.timedelta(days=REDISCOVERY_DAYS)
        rested = set(
            db.scalars(
                select(TrackAffinity.video_id).where(TrackAffinity.last_played_at <= cutoff)
            ).all()
        )
        rediscoverable = eligible_ids & rested
        if len(rediscoverable) >= request.length:
            eligible_ids = rediscoverable
        else:
            rediscover_boost_ids = rediscoverable
            mood_relaxations.append("CONTEXT_WIDENED")
    elif request.mood in UNSUPPORTED_MOODS:
        mood_relaxations.append("CONTEXT_WIDENED")

    tracks = {video_id: playable[video_id] for video_id in eligible_ids}
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
        support = support_by_id.get(video_id)
        source_type = support.best_source if support else "LIKED"
        seed_video_id = support.best_seed if support else None
        rank = support.best_rank if support else 1
        if video_id in liked:
            # A like is a root of the graph, not a candidate reached from one.
            # Turning up at position fifteen of some other track's radio must
            # not discount it — that used to push most favourites below the
            # publishing quality floor.
            source_type = "LIKED"
            support = None
            rank = 1

        artist_id = primary_artists.get(video_id)
        artist_row = artist_affinity_rows.get(artist_id) if artist_id else None
        artist_affinity = min(1.0, max(0.0, artist_row.decayed_reward)) if artist_row else 0.0
        # Position in the source list stays the base signal; agreement between
        # several liked seeds is a bonus on top. Replacing one with the other
        # shifts the whole score scale and breaks the shared quality floor.
        rank_affinity = 1.0 / (1.0 + max(0, rank - 1) * 0.1)
        seed_affinity = rank_affinity
        if support is not None:
            seed_affinity = min(1.0, rank_affinity * (1.0 + SUPPORT_BONUS * support.support_score))

        features = _build_features(
            source_type=source_type,
            seed_affinity=seed_affinity,
            artist_affinity=artist_affinity,
            support=support,
            affinity=affinities.get(video_id),
            temperature=request.temperature,
            now=now,
            liked=video_id in liked,
        )
        rule_value = rule_score(features)
        affinity_row = affinities.get(video_id)
        seed_support = 0
        if support is not None:
            seed_support = support.positive_seeds or support.distinct_seeds
        vector = build_features(
            is_liked=video_id in liked,
            artist_decayed_reward=(artist_row.decayed_reward if artist_row else 0.0),
            seed_mean_reward=affinity_row.decayed_reward if affinity_row else 0.0,
            distinct_seed_count=seed_support,
            best_source_rank=rank,
            plays_all=affinity_row.plays_all if affinity_row else 0,
            last_played_at=affinity_row.last_played_at if affinity_row else None,
            plays_7d=affinity_row.plays_7d if affinity_row else 0,
            artist_plays_7d=(artist_row.plays_7d if artist_row else 0),
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

        familiar = video_id in familiar_pool

        # Anything a recent wave already offered starts from further back.
        history_position = freshness.history_index.get(video_id)
        penalties = FAMILIAR_HISTORY_PENALTY if familiar else HISTORY_PENALTY
        if history_position is not None and history_position < len(penalties):
            score -= penalties[history_position]

        if not familiar and artist_id is not None:
            artist_position = artist_history.get(artist_id)
            if artist_position is not None and artist_position < len(ARTIST_HISTORY_PENALTY):
                score -= ARTIST_HISTORY_PENALTY[artist_position]

        # Rediscover fell back to a soft boost: applied after the model score
        # so it works in every serving phase, like the history penalties.
        if video_id in rediscover_boost_ids:
            score += REDISCOVER_BOOST

        reasons = _reason_codes(
            familiar=familiar,
            liked=video_id in liked,
            source_type=source_type,
            support=support,
            affinity=affinities.get(video_id),
            now=now,
        )

        candidates.append(
            RerankCandidate(
                video_id=video_id,
                score=score,
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

    candidates.sort(key=lambda item: (-item.score, item.video_id))

    available_familiar = sum(1 for item in candidates if item.familiar)
    rotation_cap = (
        available_familiar
        if request.for_publishing
        else int(available_familiar * rotation_share(request.temperature))
    )
    effective_target = min(familiar_target, rotation_cap)

    reranked = rerank(
        candidates,
        request.length,
        effective_target,
        rng=rng,
        temperature=request.temperature,
    )

    relaxations = [*freshness.relaxations, *mood_relaxations, *reranked.relaxations]
    if effective_target < familiar_target:
        # effective_target only drops below the quota when the rotation cap
        # bites (it is min(quota, cap)), so this is always the rotation code.
        relaxations.append("FAMILIAR_ROTATION_CAP")
    if reranked.actual_familiar_count < familiar_target and available_familiar < familiar_target:
        relaxations.append("FAMILIAR_POOL_WIDENED")

    queue_id = str(uuid.uuid4())
    generation_id = str(uuid.uuid4())
    actual_percent = (
        round(100 * reranked.actual_familiar_count / len(reranked.items)) if reranked.items else 0
    )
    chosen_ids = {item.video_id for item in reranked.items}
    previous_wave = history[0] if history else set()
    overlap_percent = (
        round(100 * len(chosen_ids & previous_wave) / len(chosen_ids)) if chosen_ids else 0
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
        relaxations_json={"codes": list(dict.fromkeys(relaxations))},
        overlap_previous_percent=overlap_percent,
        pool_size=pool_size,
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
        relaxations=tuple(dict.fromkeys(relaxations)),
        items=tuple(items),
        pool_size=pool_size,
        overlap_previous_percent=overlap_percent,
    )


def _reason_codes(
    *,
    familiar: bool,
    liked: bool,
    source_type: str,
    support: CandidateSupport | None,
    affinity: TrackAffinity | None,
    now: dt.datetime,
) -> tuple[str, ...]:
    """Deterministic explanations, never generated text (docs/06 section 4)."""
    codes: list[str] = []
    if liked:
        codes.append("LIKED_TRACK")
    elif familiar:
        codes.append("STRONG_LOCAL_SIGNAL")

    if support is not None and support.positive_seeds >= 2:
        codes.append("MANY_SEEDS_AGREE")
    elif source_type == "RELATED":
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
