"""The candidate graph and its expansion (docs/05 section 3).

The pool used to be a seven-day cache of one-hop neighbours around a few
likes, which capped it at a couple of hundred tracks. These tests pin down
the three properties that lift that ceiling: edges persist, support is
counted rather than collapsed, and expansion walks outwards under a budget.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.catalog import CandidateSource, TrackCandidate
from app.integrations.youtube_music.ledger import DISCOVERY_CALLS_PER_DAY
from app.jobs.graph_expand import run_graph_expansion
from app.persistence.models import (
    ApiCallLedger,
    CandidateEdge,
    LibraryTrackState,
    PlaybackSession,
    Track,
    utcnow,
)
from app.recommender.graph import build_support, frontier, hop_discount, positive_roots
from tests.fakes import track as fake_track


def add_track(db: Session, video_id: str, *, liked: bool = False, playable: bool = True) -> None:
    db.add(Track(video_id=video_id, title=video_id, is_playable=playable))
    db.flush()
    if liked:
        db.add(LibraryTrackState(video_id=video_id, is_liked=True))
        db.flush()


def add_edge(
    db: Session,
    seed: str,
    candidate: str,
    *,
    hop: int = 1,
    rank: int = 1,
    expires_in_days: int = 7,
    source: str = "RADIO",
) -> None:
    now = utcnow()
    db.add(
        CandidateEdge(
            seed_video_id=seed,
            candidate_video_id=candidate,
            source_type=source,
            source_key=seed,
            rank=rank,
            hop=hop,
            fetched_at=now,
            expires_at=now + dt.timedelta(days=expires_in_days),
        )
    )
    db.flush()


def candidate(video_id: str, rank: int = 1) -> TrackCandidate:
    return TrackCandidate(
        track=fake_track(video_id, video_id),
        source=CandidateSource.RADIO,
        source_key="radio",
        rank=rank,
    )


# -- the graph is knowledge, not a cache ----------------------------------


def test_expired_edges_still_count_towards_the_pool(db_session) -> None:
    add_track(db_session, "root", liked=True)
    add_track(db_session, "old")
    add_edge(db_session, "root", "old", expires_in_days=-30)

    support = build_support(db_session)
    assert "old" in support


def test_support_counts_every_seed_that_agrees(db_session) -> None:
    for index in range(4):
        add_track(db_session, f"root-{index}", liked=True)
    add_track(db_session, "shared")
    add_track(db_session, "lonely")
    for index in range(4):
        add_edge(db_session, f"root-{index}", "shared", rank=index + 1)
    add_edge(db_session, "root-0", "lonely", rank=1)

    support = build_support(db_session)

    assert support["shared"].distinct_seeds == 4
    assert support["shared"].positive_seeds == 4
    assert support["lonely"].distinct_seeds == 1
    assert support["shared"].support_score > support["lonely"].support_score


def test_distance_from_a_liked_track_discounts_the_candidate(db_session) -> None:
    add_track(db_session, "root", liked=True)
    add_track(db_session, "near")
    add_track(db_session, "far")
    add_edge(db_session, "root", "near", hop=1)
    add_edge(db_session, "near", "far", hop=2)

    support = build_support(db_session)
    assert support["near"].min_hop == 1
    assert support["far"].min_hop == 2
    assert hop_discount(2) < hop_discount(1) == 1.0


def test_positive_roots_include_strong_rewards_and_exclude_dislikes(db_session) -> None:
    add_track(db_session, "liked", liked=True)
    add_track(db_session, "earned")
    add_track(db_session, "hated")
    db_session.add(LibraryTrackState(video_id="hated", is_disliked=True))
    db_session.flush()
    db_session.add(
        PlaybackSession(
            session_id="s1",
            video_id="earned",
            started_at=utcnow(),
            qualified=True,
            reward=0.7,
        )
    )
    db_session.flush()

    roots = positive_roots(db_session)
    assert roots == {"liked", "earned"}


# -- frontier expansion ----------------------------------------------------


def test_frontier_prefers_the_best_supported_unexplored_node(db_session) -> None:
    for index in range(3):
        add_track(db_session, f"root-{index}", liked=True)
    add_track(db_session, "popular")
    add_track(db_session, "obscure")
    for index in range(3):
        add_edge(db_session, f"root-{index}", "popular")
    add_edge(db_session, "root-0", "obscure")

    nodes = frontier(db_session, limit=2)
    assert nodes[0].video_id == "popular"


def test_frontier_skips_nodes_that_were_already_expanded(db_session) -> None:
    add_track(db_session, "root", liked=True)
    add_track(db_session, "opened")
    add_track(db_session, "fresh")
    add_edge(db_session, "root", "opened")
    add_edge(db_session, "root", "fresh")
    add_edge(db_session, "opened", "beyond", hop=2)

    ids = {node.video_id for node in frontier(db_session, limit=10)}
    assert "opened" not in ids
    assert "fresh" in ids


def test_expansion_walks_one_hop_further_out(db_session, fake_catalog) -> None:
    add_track(db_session, "root", liked=True)
    add_track(db_session, "near")
    add_edge(db_session, "root", "near", hop=1)
    fake_catalog.candidates = {"near": [candidate("far-1"), candidate("far-2")]}

    result = run_graph_expansion(db_session, fake_catalog, max_calls=4)

    assert "near" in result.nodes_expanded
    assert result.edges_written == 2
    hops = {
        row.candidate_video_id: row.hop
        for row in db_session.scalars(
            select(CandidateEdge).where(CandidateEdge.seed_video_id == "near")
        )
    }
    assert hops == {"far-1": 2, "far-2": 2}


def test_expansion_stops_when_the_discovery_budget_is_spent(db_session, fake_catalog) -> None:
    add_track(db_session, "root", liked=True)
    add_track(db_session, "near")
    add_edge(db_session, "root", "near")
    fake_catalog.candidates = {"near": [candidate("far-1")]}

    now = utcnow()
    for index in range(DISCOVERY_CALLS_PER_DAY):
        db_session.add(
            ApiCallLedger(
                provider="ytmusic",
                operation="get_watch_playlist",
                request_fingerprint="get_watch_playlist",
                is_mutation=False,
                counts_against_automatic_budget=True,
                started_at=now - dt.timedelta(minutes=index),
                finished_at=now,
                duration_ms=10,
                outcome="SUCCESS",
            )
        )
    db_session.flush()

    result = run_graph_expansion(db_session, fake_catalog)
    assert result.calls_made == 0
    assert result.nodes_expanded == ()


def test_expansion_grows_the_pool_across_runs(db_session, fake_catalog) -> None:
    """The point of the whole exercise: more material after every run."""
    add_track(db_session, "root", liked=True)
    fake_catalog.candidates = {
        "root": [candidate(f"a{index}", rank=index + 1) for index in range(5)],
        "a0": [candidate(f"b{index}", rank=index + 1) for index in range(5)],
        "a1": [candidate(f"c{index}", rank=index + 1) for index in range(5)],
    }
    add_edge(db_session, "root", "a0")
    add_edge(db_session, "root", "a1")

    before = len(build_support(db_session))
    run_graph_expansion(db_session, fake_catalog, max_calls=2)
    after = len(build_support(db_session))

    assert after > before


# -- breadth before depth --------------------------------------------------


def test_an_unexplored_favourite_outranks_a_well_supported_candidate(db_session) -> None:
    """A like nobody's radio mentions is absent from support entirely, so it
    could never be picked — and the walk kept deepening one cluster."""
    add_track(db_session, "root", liked=True)
    add_track(db_session, "lonely-like", liked=True)
    add_track(db_session, "popular")
    for index in range(4):
        add_track(db_session, f"other-root-{index}", liked=True)
        add_edge(db_session, f"other-root-{index}", "popular")
    add_edge(db_session, "root", "popular")

    nodes = frontier(db_session, limit=10)
    assert nodes[0].video_id == "lonely-like"
    assert "popular" in {node.video_id for node in nodes}


def test_every_favourite_gets_explored_before_going_deeper(db_session) -> None:
    for index in range(6):
        add_track(db_session, f"like-{index}", liked=True)
    add_track(db_session, "reached")
    add_edge(db_session, "like-0", "reached")

    ids = [node.video_id for node in frontier(db_session, limit=10)]
    unexplored_likes = {f"like-{index}" for index in range(1, 6)}
    assert set(ids[: len(unexplored_likes)]) == unexplored_likes
