"""Affinity aggregates ``affinity-v1`` (docs/07 section 4).

Playback sessions carry the truth, but ranking cannot afford to scan them on
every wave, so they are folded into one row per track and one row per artist.

Two properties matter more than speed here:

* **Idempotence.** A telemetry batch may be replayed, and the session summary
  is recomputed from raw events every time. Aggregates are therefore rebuilt
  from all sessions of the track rather than incremented, so a replay can
  never inflate a counter.
* **Recency.** ``decayed_reward`` is an exponentially weighted mean with a
  thirty day half life: what you thought of a track last week outweighs what
  you thought of it in spring, without ever fully forgetting.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.persistence.models import (
    ArtistAffinity,
    PlaybackSession,
    TrackAffinity,
    TrackArtist,
    utcnow,
)

AGGREGATE_VERSION = "affinity-v1"

REWARD_HALF_LIFE_DAYS = 30.0
CONFIDENCE_SATURATION = 20.0


def _decay_weight(age: dt.timedelta) -> float:
    days = max(0.0, age.total_seconds() / 86400.0)
    return float(0.5 ** (days / REWARD_HALF_LIFE_DAYS))


def _decayed_mean(rows: list[tuple[float, dt.datetime]], now: dt.datetime) -> float:
    """Exponentially weighted mean reward, or 0.0 when nothing is known."""
    total_weight = 0.0
    total = 0.0
    for reward, observed_at in rows:
        weight = _decay_weight(now - observed_at)
        total += weight * reward
        total_weight += weight
    if total_weight <= 0.0:
        return 0.0
    return max(-1.0, min(1.0, total / total_weight))


def _sessions_for(db: Session, video_ids: list[str]) -> list[PlaybackSession]:
    if not video_ids:
        return []
    return list(
        db.scalars(select(PlaybackSession).where(PlaybackSession.video_id.in_(video_ids))).all()
    )


def _window_counts(sessions: list[PlaybackSession], now: dt.datetime) -> tuple[int, int, int, int]:
    day = now - dt.timedelta(days=1)
    week = now - dt.timedelta(days=7)
    month = now - dt.timedelta(days=30)
    plays_1d = plays_7d = plays_30d = plays_all = 0
    for session in sessions:
        if not session.qualified:
            continue
        plays_all += 1
        if session.started_at >= month:
            plays_30d += 1
            if session.started_at >= week:
                plays_7d += 1
                if session.started_at >= day:
                    plays_1d += 1
    return plays_1d, plays_7d, plays_30d, plays_all


def _is_skip(session: PlaybackSession) -> bool:
    return bool(session.early_skip or session.mid_skip)


def recompute_track_affinity(
    db: Session, video_id: str, *, now: dt.datetime | None = None
) -> TrackAffinity:
    """Rebuild one track's aggregate from every session it ever had."""
    now = now or utcnow()
    sessions = _sessions_for(db, [video_id])

    plays_1d, plays_7d, plays_30d, plays_all = _window_counts(sessions, now)
    rewards = [
        (float(s.reward), s.started_at) for s in sessions if s.qualified and s.reward is not None
    ]

    row = db.get(TrackAffinity, video_id)
    if row is None:
        row = TrackAffinity(video_id=video_id)
        db.add(row)

    row.plays_1d = plays_1d
    row.plays_7d = plays_7d
    row.plays_30d = plays_30d
    row.plays_all = plays_all
    row.completions = sum(1 for s in sessions if s.completed)
    row.skips = sum(1 for s in sessions if _is_skip(s))
    row.replays = sum(1 for s in sessions if s.replayed)
    row.decayed_reward = _decayed_mean(rewards, now)
    row.last_played_at = max((s.started_at for s in sessions if s.qualified), default=None)
    row.last_liked_at = max(
        (s.started_at for s in sessions if s.explicit_rating == "LIKE"), default=None
    )
    row.last_skipped_at = max((s.started_at for s in sessions if _is_skip(s)), default=None)
    row.confidence = min(1.0, plays_all / CONFIDENCE_SATURATION)
    row.aggregate_version = AGGREGATE_VERSION
    row.updated_at = now
    return row


def _artists_of(db: Session, video_id: str) -> list[str]:
    """Every credited artist, not just the primary one — a feature that pulls
    you in still counts as exposure to that artist."""
    return list(
        db.scalars(select(TrackArtist.artist_id).where(TrackArtist.track_id == video_id)).all()
    )


def _tracks_of(db: Session, artist_id: str) -> list[str]:
    return list(
        db.scalars(select(TrackArtist.track_id).where(TrackArtist.artist_id == artist_id)).all()
    )


def recompute_artist_affinity(
    db: Session, artist_id: str, *, now: dt.datetime | None = None
) -> ArtistAffinity:
    now = now or utcnow()
    sessions = _sessions_for(db, _tracks_of(db, artist_id))

    plays_1d, plays_7d, plays_30d, plays_all = _window_counts(sessions, now)
    rewards = [
        (float(s.reward), s.started_at) for s in sessions if s.qualified and s.reward is not None
    ]

    row = db.get(ArtistAffinity, artist_id)
    if row is None:
        row = ArtistAffinity(artist_id=artist_id)
        db.add(row)

    row.plays_1d = plays_1d
    row.plays_7d = plays_7d
    row.plays_30d = plays_30d
    row.plays_all = plays_all
    row.decayed_reward = _decayed_mean(rewards, now)
    row.last_played_at = max((s.started_at for s in sessions if s.qualified), default=None)
    row.confidence = min(1.0, plays_all / CONFIDENCE_SATURATION)
    row.updated_at = now
    return row


def refresh_for_track(db: Session, video_id: str, *, now: dt.datetime | None = None) -> None:
    """Update the track and every artist credited on it.

    Called straight after a session is re-aggregated: the wave adapts on the
    next track, not on the next day.
    """
    now = now or utcnow()
    recompute_track_affinity(db, video_id, now=now)
    for artist_id in _artists_of(db, video_id):
        recompute_artist_affinity(db, artist_id, now=now)


def rebuild_all(db: Session, *, now: dt.datetime | None = None) -> tuple[int, int]:
    """Full rebuild — used by the periodic job and by the initial backfill.

    The rolling windows shrink with time even when nothing is played, so the
    aggregates need a periodic pass regardless of new telemetry.
    """
    now = now or utcnow()
    video_ids = list(db.scalars(select(PlaybackSession.video_id).distinct()).all())
    for video_id in video_ids:
        recompute_track_affinity(db, video_id, now=now)

    artist_ids = set()
    if video_ids:
        artist_ids = set(
            db.scalars(
                select(TrackArtist.artist_id).where(TrackArtist.track_id.in_(video_ids)).distinct()
            ).all()
        )
    for artist_id in artist_ids:
        recompute_artist_affinity(db, artist_id, now=now)

    db.flush()
    return len(video_ids), len(artist_ids)
