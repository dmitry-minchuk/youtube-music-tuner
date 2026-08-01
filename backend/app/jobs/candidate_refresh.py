"""Candidate pool refresh (docs/03 section 7, docs/05 section 3).

At most five seeds per run, at most one related and one radio call per seed
and only when the seven day TTL has expired. Ranking never triggers this:
opening the Wave works purely from the stored pool.
"""

from __future__ import annotations

import datetime as dt
import logging
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog import CandidateSource
from app.integrations.youtube_music.errors import IntegrationError
from app.integrations.youtube_music.port import MusicCatalogPort
from app.persistence import repositories as repo
from app.persistence.models import (
    CandidateEdge,
    PlaybackSession,
    TrackAffinity,
    TrackArtist,
    utcnow,
)
from app.recommender.graph import positive_roots

logger = logging.getLogger(__name__)

MAX_SEEDS_PER_RUN = 6
MAX_SEEDS_PER_ARTIST = 2
EDGE_TTL = dt.timedelta(days=7)
RADIO_LIMIT = 25
LONG_UNPLAYED_DAYS = 45
STRONG_POSITIVE_REWARD = 0.4


@dataclass(frozen=True, slots=True)
class RefreshResult:
    seeds: tuple[str, ...]
    edges_written: int
    calls_made: int


def _artist_of(db: Session, video_ids: list[str]) -> dict[str, str]:
    if not video_ids:
        return {}
    rows = db.execute(
        select(TrackArtist.track_id, TrackArtist.artist_id).where(
            TrackArtist.track_id.in_(video_ids), TrackArtist.ordinal == 0
        )
    ).all()
    return {track: artist for track, artist in rows}


def select_seeds(db: Session, now: dt.datetime, rng: random.Random) -> list[str]:
    """Two long-unplayed favourites, two fresh discoveries, two random positives.

    Seeds are not limited to likes: a track that earned a strong reward is
    just as good a place to explore from, and there are far more of those.
    """
    positives = sorted(positive_roots(db))
    if not positives:
        return []

    affinity = {
        row.video_id: row
        for row in db.scalars(select(TrackAffinity).where(TrackAffinity.video_id.in_(positives)))
    }

    def last_played(video_id: str) -> dt.datetime:
        row = affinity.get(video_id)
        return row.last_played_at if row and row.last_played_at else dt.datetime.min

    long_unplayed = sorted(positives, key=lambda video: (last_played(video), video))[:2]

    fresh_discovery = list(
        db.scalars(
            select(PlaybackSession.video_id)
            .where(
                PlaybackSession.qualified.is_(True),
                PlaybackSession.reward >= STRONG_POSITIVE_REWARD,
                PlaybackSession.started_at >= now - dt.timedelta(days=7),
            )
            .order_by(PlaybackSession.started_at.desc())
            .limit(2)
        ).all()
    )

    remaining = sorted(set(positives) - set(long_unplayed) - set(fresh_discovery))
    random_positive = rng.sample(remaining, k=min(2, len(remaining))) if remaining else []

    ordered: list[str] = []
    for video_id in [*long_unplayed, *fresh_discovery, *random_positive]:
        if video_id not in ordered:
            ordered.append(video_id)

    # No artist may contribute more than two seeds in one run.
    artists = _artist_of(db, ordered)
    per_artist: dict[str, int] = {}
    seeds: list[str] = []
    for video_id in ordered:
        artist = artists.get(video_id, video_id)
        if per_artist.get(artist, 0) >= MAX_SEEDS_PER_ARTIST:
            continue
        per_artist[artist] = per_artist.get(artist, 0) + 1
        seeds.append(video_id)
        if len(seeds) >= MAX_SEEDS_PER_RUN:
            break
    return seeds


def _has_fresh_edges(db: Session, seed: str, source: CandidateSource, now: dt.datetime) -> bool:
    existing = db.scalar(
        select(CandidateEdge).where(
            CandidateEdge.seed_video_id == seed,
            CandidateEdge.source_type == source.value,
            CandidateEdge.expires_at > now,
        )
    )
    return existing is not None


def store_edges(
    db: Session, seed: str, candidates: list, now: dt.datetime, *, hop: int = 1
) -> int:
    """Write the edges a fetch produced. Existing edges are refreshed, never
    duplicated, and an edge is only ever moved closer to the roots."""
    written = 0
    expires_at = now + EDGE_TTL
    for candidate in candidates:
        track = candidate.track
        if track.video_id is None or track.video_id == seed:
            continue
        repo.upsert_track(db, track)
        existing = db.scalar(
            select(CandidateEdge).where(
                CandidateEdge.seed_video_id == seed,
                CandidateEdge.candidate_video_id == track.video_id,
                CandidateEdge.source_type == candidate.source.value,
                CandidateEdge.source_key == candidate.source_key,
            )
        )
        if existing is not None:
            existing.rank = candidate.rank
            existing.fetched_at = now
            existing.expires_at = expires_at
            existing.hop = min(existing.hop, hop)
            continue
        db.add(
            CandidateEdge(
                seed_video_id=seed,
                candidate_video_id=track.video_id,
                source_type=candidate.source.value,
                source_key=candidate.source_key,
                rank=candidate.rank,
                fetched_at=now,
                expires_at=expires_at,
                hop=hop,
            )
        )
        written += 1
    return written


def run_candidate_refresh(
    db: Session,
    catalog: MusicCatalogPort,
    *,
    random_seed: int | None = None,
) -> RefreshResult:
    now = utcnow()
    rng = random.Random(random_seed if random_seed is not None else 0)
    seeds = select_seeds(db, now, rng)

    written = 0
    calls = 0
    for seed in seeds:
        for source, fetch in (
            (CandidateSource.RELATED, lambda s=seed: catalog.related(s)),
            (CandidateSource.RADIO, lambda s=seed: catalog.radio(s, RADIO_LIMIT)),
        ):
            if _has_fresh_edges(db, seed, source, now):
                continue
            try:
                candidates = fetch()
            except IntegrationError as exc:
                logger.warning(
                    "candidate fetch failed",
                    extra={
                        "operation": "candidate_refresh",
                        "error_code": exc.code,
                        "outcome": "FAILED",
                    },
                )
                continue
            calls += 1
            # Seeds are positive roots, so their neighbours sit one hop out.
            written += store_edges(db, seed, candidates, now, hop=1)

    db.flush()
    logger.info(
        "candidate refresh finished",
        extra={
            "operation": "candidate_refresh",
            "outcome": "SUCCESS",
            "seeds": len(seeds),
            "edges": written,
        },
    )
    return RefreshResult(seeds=tuple(seeds), edges_written=written, calls_made=calls)


def prune_expired_edges(db: Session, now: dt.datetime | None = None) -> int:
    """Edges are deleted 30 days after expiry (docs/07 section 8)."""
    now = now or utcnow()
    cutoff = now - dt.timedelta(days=30)
    deleted = (
        db.query(CandidateEdge)
        .filter(CandidateEdge.expires_at < cutoff)
        .delete(synchronize_session=False)
    )
    return int(deleted)
