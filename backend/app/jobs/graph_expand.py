"""Frontier expansion of the candidate graph (docs/05 section 3).

The daily seed refresh only ever looks one hop away from a handful of
favourites, which caps the pool at a few hundred tracks — far too few for a
wave of forty to stay fresh. This job widens the graph instead: every few
hours it opens the most promising *unexplored* nodes and stores their
neighbours one hop further out.

Radio is used rather than related because it costs a single call and returns
up to twenty five candidates, which is the best material-per-call ratio the
API offers.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.youtube_music.errors import IntegrationError
from app.integrations.youtube_music.ledger import (
    GRAPH_EXPAND_CALLS_PER_RUN,
    CallBudget,
)
from app.integrations.youtube_music.port import MusicCatalogPort
from app.jobs.candidate_refresh import RADIO_LIMIT, store_edges
from app.persistence.models import CandidateEdge, utcnow
from app.recommender.graph import MAX_HOP, build_support, frontier, graph_size

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    nodes_expanded: tuple[str, ...]
    edges_written: int
    calls_made: int
    edges_total: int
    candidates_total: int


def _stale_reexpansion(db: Session, now: dt.datetime, limit: int) -> list[tuple[str, int]]:
    """When the frontier is exhausted, refresh the oldest explored nodes.

    Their neighbourhoods drift over time, so re-reading them still brings in
    material the pool has never seen.
    """
    rows = db.execute(
        select(CandidateEdge.seed_video_id, CandidateEdge.hop)
        .where(CandidateEdge.expires_at <= now)
        .order_by(CandidateEdge.expires_at)
    ).all()

    seen: dict[str, int] = {}
    for seed, hop in rows:
        # A re-expanded node keeps the distance it already had.
        seen.setdefault(seed, max(0, hop - 1))
        if len(seen) >= limit:
            break
    return list(seen.items())


def run_graph_expansion(
    db: Session,
    catalog: MusicCatalogPort,
    *,
    max_calls: int = GRAPH_EXPAND_CALLS_PER_RUN,
) -> ExpansionResult:
    now = utcnow()
    budget = CallBudget(db, now)
    allowed = min(max_calls, budget.remaining_discovery_calls())
    if allowed <= 0:
        edges, distinct = graph_size(db)
        logger.info(
            "graph expansion skipped, discovery budget spent",
            extra={"operation": "graph_expand", "outcome": "SKIPPED"},
        )
        return ExpansionResult((), 0, 0, edges, distinct)

    support = build_support(db, now=now)
    targets: list[tuple[str, int]] = [
        (node.video_id, node.hop)
        for node in frontier(db, limit=allowed, max_hop=MAX_HOP, support=support)
    ]
    if not targets:
        targets = _stale_reexpansion(db, now, allowed)

    expanded: list[str] = []
    written = 0
    calls = 0
    for video_id, hop in targets:
        try:
            neighbours = catalog.radio(video_id, RADIO_LIMIT)
        except IntegrationError as exc:
            logger.warning(
                "graph expansion fetch failed",
                extra={
                    "operation": "graph_expand",
                    "error_code": exc.code,
                    "outcome": "FAILED",
                },
            )
            continue
        calls += 1
        # Radio of a hop-N node yields hop-(N+1) neighbours.
        written += store_edges(db, video_id, neighbours, now, hop=min(MAX_HOP, max(1, hop + 1)))
        expanded.append(video_id)

    db.flush()
    edges, distinct = graph_size(db)
    logger.info(
        "graph expansion finished",
        extra={
            "operation": "graph_expand",
            "outcome": "SUCCESS",
            "nodes": len(expanded),
            "edges": written,
            "graph_edges": edges,
            "graph_candidates": distinct,
        },
    )
    return ExpansionResult(tuple(expanded), written, calls, edges, distinct)
