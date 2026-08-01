"""Playlist quality gates ``playlist-gates-v2`` (docs/05 section 12).

The planner picks the largest deterministically reachable size between
``min_publish_size`` and the configured target, so a small library degrades
to a shorter playlist with an explicit reason instead of failing forever.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

QUALITY_GATE_VERSION = "playlist-gates-v2"

MIN_PUBLISH_SIZE = 25
DEFAULT_TARGET_SIZE = 60
QUOTA_TOLERANCE_POINTS = 10
MIN_ARTIST_RATIO = 0.40
MAX_TRACKS_PER_ARTIST = 3
MAX_SEED_SHARE = 0.20
QUALITY_EXPECTED_FLOOR = -0.20
COMPARATIVE_TOLERANCE = 0.05


@dataclass(frozen=True, slots=True)
class GateCandidate:
    video_id: str
    artist_id: str | None
    familiar: bool
    quality_expected: float
    seed_video_id: str | None
    is_disliked: bool = False
    is_blocked: bool = False
    is_playable: bool = True
    has_error_cooldown: bool = False
    is_metadata_only: bool = False
    discovery_edge_expired: bool = False
    has_source_reason: bool = True


@dataclass(frozen=True, slots=True)
class GateOutcome:
    passed: bool
    effective_target_size: int
    failures: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    required_familiar: int = 0
    available_familiar: int = 0
    required_discovery: int = 0
    available_discovery: int = 0
    reasons: tuple[str, ...] = field(default=())


def _min_familiar_for(size: int, target_percent: int) -> int:
    """Lowest familiar count still inside the ±10 point tolerance."""
    lowest_share = max(0, target_percent - QUOTA_TOLERANCE_POINTS) / 100
    return math.ceil(lowest_share * size)


def _max_familiar_for(size: int, target_percent: int) -> int:
    highest_share = min(100, target_percent + QUOTA_TOLERANCE_POINTS) / 100
    return math.floor(highest_share * size)


def largest_feasible_size(
    *,
    configured_target_size: int,
    target_familiar_percent: int,
    available_familiar: int,
    available_discovery: int,
) -> int | None:
    """Biggest size where both buckets can satisfy the quota tolerance."""
    for size in range(configured_target_size, MIN_PUBLISH_SIZE - 1, -1):
        min_familiar = _min_familiar_for(size, target_familiar_percent)
        max_familiar = _max_familiar_for(size, target_familiar_percent)
        usable_familiar = min(available_familiar, max_familiar)
        if usable_familiar < min_familiar:
            continue
        if usable_familiar + available_discovery < size:
            continue
        if size - usable_familiar > available_discovery:
            continue
        return size
    return None


def evaluate(
    candidates: list[GateCandidate],
    *,
    configured_target_size: int,
    target_familiar_percent: int,
    comparable_remote_mean: float | None,
    score_policy_changed: bool = False,
) -> GateOutcome:
    """Run every gate against a desired list of exactly effective size."""
    failures: list[str] = []
    skipped: list[str] = []
    reasons: list[str] = []

    size = len(candidates)
    if size < MIN_PUBLISH_SIZE or size > configured_target_size:
        failures.append("SIZE_OUT_OF_RANGE")

    video_ids = [candidate.video_id for candidate in candidates]
    if len(set(video_ids)) != len(video_ids):
        failures.append("DUPLICATE_TRACKS")

    if any(
        candidate.is_disliked
        or candidate.is_blocked
        or candidate.has_error_cooldown
        or candidate.is_metadata_only
        or not candidate.is_playable
        for candidate in candidates
    ):
        failures.append("INELIGIBLE_TRACK")

    familiar_count = sum(1 for candidate in candidates if candidate.familiar)
    if size:
        actual_percent = 100 * familiar_count / size
        if abs(actual_percent - target_familiar_percent) > QUOTA_TOLERANCE_POINTS:
            failures.append("QUOTA_OUT_OF_TOLERANCE")

    artists = [candidate.artist_id for candidate in candidates if candidate.artist_id]
    if len(set(artists)) < math.ceil(MIN_ARTIST_RATIO * size):
        failures.append("NOT_ENOUGH_ARTISTS")
    if artists and max(artists.count(artist) for artist in set(artists)) > MAX_TRACKS_PER_ARTIST:
        failures.append("ARTIST_OVER_REPRESENTED")
    if any(
        left.artist_id is not None and left.artist_id == right.artist_id
        for left, right in zip(candidates, candidates[1:], strict=False)
    ):
        failures.append("ADJACENT_SAME_ARTIST")

    if any(candidate.quality_expected < QUALITY_EXPECTED_FLOOR for candidate in candidates):
        failures.append("NEGATIVE_QUALITY_EXPECTED")

    if score_policy_changed:
        skipped.append("INCOMPATIBLE_SCORE_POLICY")
    elif comparable_remote_mean is None:
        skipped.append("NO_COMPARABLE_REMOTE_SCORE")
    elif candidates:
        mean_quality = sum(c.quality_expected for c in candidates) / len(candidates)
        if mean_quality < comparable_remote_mean - COMPARATIVE_TOLERANCE:
            failures.append("WORSE_THAN_CURRENT_PLAYLIST")

    discovery = [candidate for candidate in candidates if not candidate.familiar]
    if any(candidate.discovery_edge_expired for candidate in discovery):
        failures.append("EXPIRED_DISCOVERY_EDGE")
    if any(not candidate.has_source_reason for candidate in discovery):
        failures.append("DISCOVERY_WITHOUT_SOURCE")

    seeds = [candidate.seed_video_id for candidate in candidates if candidate.seed_video_id]
    if seeds and size:
        dominant = max(seeds.count(seed) for seed in set(seeds))
        if dominant / size > MAX_SEED_SHARE:
            failures.append("SEED_OVER_REPRESENTED")

    if size < configured_target_size and not failures:
        reasons.append("TARGET_SIZE_REDUCED_FOR_POOL")

    return GateOutcome(
        passed=not failures,
        effective_target_size=size,
        failures=tuple(failures),
        skipped=tuple(skipped),
        required_familiar=_min_familiar_for(size, target_familiar_percent),
        available_familiar=familiar_count,
        required_discovery=size - _max_familiar_for(size, target_familiar_percent),
        available_discovery=size - familiar_count,
        reasons=tuple(reasons),
    )
