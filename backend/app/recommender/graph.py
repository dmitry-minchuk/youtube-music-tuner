"""The candidate graph (docs/05 section 3).

A single-user installation cannot do collaborative filtering: there is no
"users like you". What it *can* do is read a collaborative signal that
YouTube already computed over millions of listeners — the related/radio edges
between tracks — and treat it as a graph to walk rather than a one-shot
lookup.

Two ideas carry the weight here:

* **Support.** A candidate reached from five different tracks you like is a
  far stronger recommendation than one reached from a single track. This is
  the co-occurrence count that item embedding methods learn from, available
  to us directly as the in-degree from positive roots.
* **Distance.** Edges are followed several hops out from the roots, with the
  contribution discounted per hop, so the pool keeps widening without drifting
  into unrelated music.

Nothing here calls YouTube. The graph is read from what previous refresh and
expansion runs stored.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.persistence.models import (
    CandidateEdge,
    LibraryTrackState,
    PlaybackSession,
    Track,
    TrackAffinity,
    utcnow,
)

MAX_HOP = 3
HOP_DECAY = 0.72

# A track counts as a positive root once it earned this much reward.
STRONG_POSITIVE_REWARD = 0.4

SUPPORT_SATURATION = 5.0


@dataclass(frozen=True, slots=True)
class CandidateSupport:
    """What the graph knows about one candidate."""

    video_id: str
    distinct_seeds: int
    positive_seeds: int
    best_rank: int
    min_hop: int
    best_source: str
    best_seed: str | None
    seed_reward: float
    support_score: float


def hop_discount(hop: int) -> float:
    return float(HOP_DECAY ** max(0, hop - 1))


def positive_roots(db: Session) -> set[str]:
    """Likes plus anything that earned a strong reward locally."""
    liked = set(
        db.scalars(
            select(LibraryTrackState.video_id).where(LibraryTrackState.is_liked.is_(True))
        ).all()
    )
    strong = set(
        db.scalars(
            select(PlaybackSession.video_id).where(
                PlaybackSession.qualified.is_(True),
                PlaybackSession.reward.is_not(None),
                PlaybackSession.reward >= STRONG_POSITIVE_REWARD,
            )
        ).all()
    )
    disliked = set(
        db.scalars(
            select(LibraryTrackState.video_id).where(LibraryTrackState.is_disliked.is_(True))
        ).all()
    )
    return (liked | strong) - disliked


def _seed_weights(db: Session, roots: set[str]) -> dict[str, float]:
    """How much a seed's opinion is worth, in [0, 1].

    A liked track is a full vote. Everything else is worth its decayed reward
    mapped onto [0, 1], so a seed you keep skipping stops pulling its
    neighbourhood into the pool.
    """
    weights: dict[str, float] = {}
    for row in db.scalars(select(TrackAffinity)):
        weights[row.video_id] = max(0.0, min(1.0, (row.decayed_reward + 1.0) / 2.0))
    for video_id in roots:
        weights[video_id] = max(weights.get(video_id, 0.0), 0.85)
    return weights


def build_support(
    db: Session, *, now: dt.datetime | None = None
) -> dict[str, CandidateSupport]:
    """Fold every stored edge into one row per candidate.

    Unlike the previous "best edge wins" collapse, the multiplicity is kept:
    it is the strongest signal the graph carries.
    """
    now = now or utcnow()
    roots = positive_roots(db)
    weights = _seed_weights(db, roots)

    seeds_by_candidate: dict[str, set[str]] = {}
    positive_by_candidate: dict[str, set[str]] = {}
    best: dict[str, tuple[int, int, str, str]] = {}
    reward_sum: dict[str, float] = {}
    reward_weight: dict[str, float] = {}

    for edge in db.scalars(select(CandidateEdge)):
        candidate = edge.candidate_video_id
        seeds_by_candidate.setdefault(candidate, set()).add(edge.seed_video_id)
        if edge.seed_video_id in roots:
            positive_by_candidate.setdefault(candidate, set()).add(edge.seed_video_id)

        current = best.get(candidate)
        key = (edge.hop, edge.rank)
        if current is None or key < (current[1], current[0]):
            best[candidate] = (edge.rank, edge.hop, edge.source_type, edge.seed_video_id)

        weight = weights.get(edge.seed_video_id, 0.4) * hop_discount(edge.hop)
        reward_sum[candidate] = reward_sum.get(candidate, 0.0) + weight
        reward_weight[candidate] = reward_weight.get(candidate, 0.0) + 1.0

    support: dict[str, CandidateSupport] = {}
    for candidate, seeds in seeds_by_candidate.items():
        rank, hop, source, seed = best[candidate]
        positive = len(positive_by_candidate.get(candidate, ()))
        mean_weight = reward_sum[candidate] / max(1.0, reward_weight[candidate])
        # Diminishing returns: the jump from one supporting seed to two says
        # much more than the jump from nine to ten.
        breadth = min(1.0, (positive or len(seeds)) / SUPPORT_SATURATION)
        support[candidate] = CandidateSupport(
            video_id=candidate,
            distinct_seeds=len(seeds),
            positive_seeds=positive,
            best_rank=rank,
            min_hop=hop,
            best_source=source,
            best_seed=seed,
            seed_reward=mean_weight,
            support_score=breadth * mean_weight,
        )
    return support


@dataclass(frozen=True, slots=True)
class FrontierNode:
    video_id: str
    hop: int
    priority: float


def frontier(
    db: Session,
    *,
    limit: int,
    max_hop: int = MAX_HOP,
    support: dict[str, CandidateSupport] | None = None,
) -> list[FrontierNode]:
    """Nodes worth spending an external call on, best first.

    Expansion is a bounded breadth-first walk: nodes with the most support
    from tracks you actually like get opened first, so the budget is never
    spent on a random corner of the graph.
    """
    support = support if support is not None else build_support(db)
    expanded = set(db.scalars(select(CandidateEdge.seed_video_id).distinct()).all())
    disliked = set(
        db.scalars(
            select(LibraryTrackState.video_id).where(LibraryTrackState.is_disliked.is_(True))
        ).all()
    )
    negative = set(
        db.scalars(select(TrackAffinity.video_id).where(TrackAffinity.decayed_reward < 0.0)).all()
    )
    playable = set(
        db.scalars(
            select(Track.video_id).where(
                Track.is_playable.is_(True), Track.remote_deleted_at.is_(None)
            )
        ).all()
    )

    nodes: list[FrontierNode] = []
    for row in support.values():
        if row.video_id in expanded or row.video_id in disliked or row.video_id in negative:
            continue
        if row.min_hop >= max_hop:
            continue
        # Unplayable tracks are still valid graph nodes — YouTube's notion of
        # similarity does not care whether we may embed them — but a playable
        # one is preferred because it can also enter the wave itself.
        bonus = 1.0 if row.video_id in playable else 0.85
        nodes.append(
            FrontierNode(
                video_id=row.video_id,
                hop=row.min_hop,
                priority=row.support_score * hop_discount(row.min_hop) * bonus,
            )
        )

    nodes.sort(key=lambda node: (-node.priority, node.hop, node.video_id))
    return nodes[:limit]


def graph_size(db: Session) -> tuple[int, int]:
    """(edges, distinct candidates) — used for logging and diagnostics."""
    edges = db.scalar(select(func.count()).select_from(CandidateEdge)) or 0
    candidates = (
        db.scalar(select(func.count(func.distinct(CandidateEdge.candidate_video_id)))) or 0
    )
    return int(edges), int(candidates)
