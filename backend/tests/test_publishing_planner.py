"""Diff planning and quality gates (docs/03 s.8, docs/05 s.12, docs/11 s.2)."""

from __future__ import annotations

import pytest

from app.publishing.planner import (
    MAX_ITEM_CHANGES_PER_WINDOW,
    RemoteItem,
    apply_operations,
    full_operations,
    plan_window,
)
from app.publishing.quality_gates import (
    MIN_PUBLISH_SIZE,
    GateCandidate,
    evaluate,
    largest_feasible_size,
)


def remote(video_ids: list[str]) -> list[RemoteItem]:
    return [RemoteItem(video_id=v, set_video_id=f"set-{v}") for v in video_ids]


def converge(start: list[str], desired: list[str], max_windows: int = 20) -> tuple[int, list[str]]:
    """Apply windows until the order matches, mimicking daily publishes."""
    current = remote(start)
    for window in range(1, max_windows + 1):
        plan = plan_window(current, desired)
        if not plan.operations:
            return window - 1, [item.video_id for item in current]
        order = apply_operations(current, list(plan.operations), desired)
        # The next window reads the playlist fresh, so every item has an id.
        current = remote(order)
        if order == desired:
            return window, order
    return max_windows + 1, [item.video_id for item in current]


# -- diff planning --------------------------------------------------------


def test_identical_lists_need_no_operations() -> None:
    plan = plan_window(remote(["a", "b", "c"]), ["a", "b", "c"])
    assert plan.operations == ()
    assert plan.complete is True


def test_removals_come_before_additions() -> None:
    operations = full_operations(remote(["a", "b"]), ["a", "c"])
    kinds = [operation.kind for operation in operations]
    assert kinds.index("REMOVE") < kinds.index("ADD")


def test_removal_carries_the_set_video_id() -> None:
    operations = full_operations(remote(["a", "b"]), ["a"])
    removal = next(op for op in operations if op.kind == "REMOVE")
    assert removal.video_id == "b"
    assert removal.set_video_id == "set-b"


def test_freshly_added_tracks_are_not_moved_in_the_same_window() -> None:
    """A move needs a setVideoId that only the next fresh read provides."""
    operations = full_operations(remote(["a"]), ["new", "a"])
    assert [op.kind for op in operations] == ["ADD"]


def test_window_is_capped_by_item_changes() -> None:
    start = [f"old{index}" for index in range(30)]
    desired = [f"new{index}" for index in range(30)]
    plan = plan_window(remote(start), desired)
    assert plan.item_change_count <= MAX_ITEM_CHANGES_PER_WINDOW
    assert plan.complete is False
    assert plan.remaining_item_changes > 0


def test_window_is_capped_by_mutating_requests() -> None:
    start = [f"t{index}" for index in range(20)]
    desired = list(reversed(start))
    plan = plan_window(remote(start), desired)
    assert plan.request_count <= 15


def test_batched_add_and_remove_cost_one_request_each() -> None:
    plan = plan_window(remote(["a", "b", "c"]), ["x", "y", "z"])
    kinds = [operation.kind for operation in plan.operations]
    assert kinds.count("REMOVE") == 3
    assert kinds.count("ADD") == 3
    # Three removes plus three adds are two requests, not six.
    assert plan.request_count == 2


def test_a_full_replacement_converges_in_a_few_windows() -> None:
    start = [f"old{index}" for index in range(60)]
    desired = [f"new{index}" for index in range(60)]
    windows, final = converge(start, desired)
    assert final == desired
    assert windows <= 12


def test_a_full_reversal_converges() -> None:
    start = [f"t{index}" for index in range(30)]
    desired = list(reversed(start))
    windows, final = converge(start, desired)
    assert final == desired
    assert windows <= 15


def test_growing_an_empty_playlist_converges() -> None:
    desired = [f"t{index}" for index in range(40)]
    windows, final = converge([], desired)
    assert final == desired
    assert windows <= 6


def test_expected_order_matches_what_applying_the_slice_produces() -> None:
    start = ["a", "b", "c", "d"]
    desired = ["d", "c", "b", "a"]
    plan = plan_window(remote(start), desired)
    assert list(plan.expected_order) == apply_operations(
        remote(start), list(plan.operations), desired
    )


# -- adaptive sizing ------------------------------------------------------


def test_full_size_is_used_when_the_pool_allows() -> None:
    size = largest_feasible_size(
        configured_target_size=60,
        target_familiar_percent=80,
        available_familiar=60,
        available_discovery=40,
    )
    assert size == 60


def test_thin_familiar_pool_reduces_the_target() -> None:
    """30 familiar tracks at a 70% floor allow 42 (docs/05 section 12)."""
    size = largest_feasible_size(
        configured_target_size=60,
        target_familiar_percent=80,
        available_familiar=30,
        available_discovery=40,
    )
    assert size == 42


def test_too_small_a_pool_yields_no_feasible_size() -> None:
    size = largest_feasible_size(
        configured_target_size=60,
        target_familiar_percent=80,
        available_familiar=14,
        available_discovery=40,
    )
    assert size is None


def test_minimum_publish_size_is_respected() -> None:
    size = largest_feasible_size(
        configured_target_size=60,
        target_familiar_percent=80,
        available_familiar=18,
        available_discovery=10,
    )
    assert size is None or size >= MIN_PUBLISH_SIZE


# -- quality gates --------------------------------------------------------


def build_candidates(
    size: int, familiar_percent: int, *, quality: float = 0.5
) -> list[GateCandidate]:
    familiar_count = round(size * familiar_percent / 100)
    candidates = []
    for index in range(size):
        candidates.append(
            GateCandidate(
                video_id=f"v{index}",
                artist_id=f"artist{index}",  # all distinct, no adjacency issue
                familiar=index < familiar_count,
                quality_expected=quality,
                seed_video_id=f"seed{index % 10}",
            )
        )
    # Interleave so familiar tracks do not sit in one block.
    return [candidates[index] for index in _interleave(size, familiar_count)]


def _interleave(size: int, familiar_count: int) -> list[int]:
    familiar = list(range(familiar_count))
    discovery = list(range(familiar_count, size))
    result = []
    while familiar or discovery:
        if familiar:
            result.append(familiar.pop(0))
        if discovery:
            result.append(discovery.pop(0))
    return result


def test_a_healthy_list_passes_every_gate() -> None:
    outcome = evaluate(
        build_candidates(60, 80),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=0.4,
    )
    assert outcome.passed is True
    assert outcome.failures == ()


def test_quota_outside_tolerance_fails() -> None:
    outcome = evaluate(
        build_candidates(60, 50),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "QUOTA_OUT_OF_TOLERANCE" in outcome.failures


def test_reduced_size_is_allowed_and_reported() -> None:
    outcome = evaluate(
        build_candidates(42, 71),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert outcome.passed is True
    assert "TARGET_SIZE_REDUCED_FOR_POOL" in outcome.reasons


def test_below_minimum_size_fails() -> None:
    outcome = evaluate(
        build_candidates(20, 80),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "SIZE_OUT_OF_RANGE" in outcome.failures


def test_disliked_candidate_fails_the_gate() -> None:
    candidates = build_candidates(60, 80)
    candidates[5] = GateCandidate(
        video_id="hated",
        artist_id="artistX",
        familiar=candidates[5].familiar,
        quality_expected=0.5,
        seed_video_id="seed1",
        is_disliked=True,
    )
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "INELIGIBLE_TRACK" in outcome.failures


def test_negative_quality_expected_fails() -> None:
    candidates = build_candidates(60, 80)
    candidates[3] = GateCandidate(
        video_id="weak",
        artist_id="artistY",
        familiar=candidates[3].familiar,
        quality_expected=-0.25,
        seed_video_id="seed2",
    )
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "NEGATIVE_QUALITY_EXPECTED" in outcome.failures


def test_quality_floor_boundary_is_inclusive() -> None:
    outcome = evaluate(
        build_candidates(60, 80, quality=-0.20),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "NEGATIVE_QUALITY_EXPECTED" not in outcome.failures


def test_duplicates_fail() -> None:
    candidates = build_candidates(60, 80)
    candidates[1] = candidates[0]
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "DUPLICATE_TRACKS" in outcome.failures


def test_artist_over_representation_fails() -> None:
    candidates = [
        GateCandidate(
            video_id=f"v{index}",
            artist_id="mono" if index % 2 == 0 else f"artist{index}",
            familiar=index < 48,
            quality_expected=0.5,
            seed_video_id=f"seed{index % 10}",
        )
        for index in range(60)
    ]
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "ARTIST_OVER_REPRESENTED" in outcome.failures


def test_adjacent_same_artist_fails() -> None:
    candidates = build_candidates(60, 80)
    first = candidates[0]
    candidates[1] = GateCandidate(
        video_id="neighbour",
        artist_id=first.artist_id,
        familiar=candidates[1].familiar,
        quality_expected=0.5,
        seed_video_id="seedZ",
    )
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "ADJACENT_SAME_ARTIST" in outcome.failures


def test_one_seed_may_not_dominate() -> None:
    candidates = [
        GateCandidate(
            video_id=f"v{index}",
            artist_id=f"artist{index}",
            familiar=index < 48,
            quality_expected=0.5,
            seed_video_id="only-seed",
        )
        for index in range(60)
    ]
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "SEED_OVER_REPRESENTED" in outcome.failures


def test_comparative_gate_is_skipped_without_a_comparable_playlist() -> None:
    outcome = evaluate(
        build_candidates(60, 80),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "NO_COMPARABLE_REMOTE_SCORE" in outcome.skipped
    assert outcome.passed is True


def test_comparative_gate_is_skipped_when_the_score_policy_changed() -> None:
    outcome = evaluate(
        build_candidates(60, 80),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=0.9,
        score_policy_changed=True,
    )
    assert "INCOMPATIBLE_SCORE_POLICY" in outcome.skipped
    assert outcome.passed is True


def test_clearly_worse_playlist_fails_the_comparative_gate() -> None:
    outcome = evaluate(
        build_candidates(60, 80, quality=0.10),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=0.50,
    )
    assert "WORSE_THAN_CURRENT_PLAYLIST" in outcome.failures


def test_slightly_worse_playlist_stays_within_tolerance() -> None:
    outcome = evaluate(
        build_candidates(60, 80, quality=0.46),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=0.50,
    )
    assert "WORSE_THAN_CURRENT_PLAYLIST" not in outcome.failures


def test_expired_discovery_edge_fails() -> None:
    candidates = build_candidates(60, 80)
    index = next(i for i, c in enumerate(candidates) if not c.familiar)
    candidates[index] = GateCandidate(
        video_id="stale",
        artist_id="artistStale",
        familiar=False,
        quality_expected=0.5,
        seed_video_id="seed3",
        discovery_edge_expired=True,
    )
    outcome = evaluate(
        candidates,
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "EXPIRED_DISCOVERY_EDGE" in outcome.failures


@pytest.mark.parametrize("size", [25, 42, 60])
def test_artist_minimum_scales_with_size(size: int) -> None:
    outcome = evaluate(
        build_candidates(size, 80),
        configured_target_size=60,
        target_familiar_percent=80,
        comparable_remote_mean=None,
    )
    assert "NOT_ENOUGH_ARTISTS" not in outcome.failures
