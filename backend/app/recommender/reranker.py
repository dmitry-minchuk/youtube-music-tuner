"""Sequence-level diversity reranking (docs/05 section 11).

Artist window rules live here and nowhere else — hard filters only decide
whether a single track is admissible at all. When the constraints would make
the queue shorter than requested they are relaxed in a fixed, deterministic
order, and every relaxation is reported as a reason code.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace

MAX_SAME_ARTIST_WINDOW_5 = 1
MAX_SAME_ARTIST_WINDOW_15 = 2
MAX_CONSECUTIVE_SAME_SOURCE = 3
MIN_DISTINCT_SEEDS_RATIO = 0.25
DISTINCT_SEED_PREFIX = 20

SAME_ARTIST_PENALTY = 0.35
SAME_ALBUM_PENALTY = 0.15
RECENT_TRACK_PENALTY = 0.25
SOURCE_CONCENTRATION_PENALTY = 0.20
QUOTA_BONUS = 0.30


@dataclass(frozen=True, slots=True)
class RerankCandidate:
    video_id: str
    score: float
    artist_id: str | None
    album_id: str | None
    seed_video_id: str | None
    source_type: str
    familiar: bool
    quality_expected: float
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiversityLimits:
    """One rung of the relaxation ladder."""

    artist_window: int = 5
    max_same_artist_window_15: int = MAX_SAME_ARTIST_WINDOW_15
    enforce_source_concentration: bool = True
    label: str | None = None


# Deterministic ladder: source concentration first, then two artists per 15
# becomes three, then the tight window shrinks from five to three.
RELAXATION_LADDER: tuple[DiversityLimits, ...] = (
    DiversityLimits(),
    DiversityLimits(enforce_source_concentration=False, label="SOURCE_CONCENTRATION_RELAXED"),
    DiversityLimits(
        enforce_source_concentration=False,
        max_same_artist_window_15=3,
        label="ARTIST_WINDOW_15_RELAXED",
    ),
    DiversityLimits(
        artist_window=3,
        enforce_source_concentration=False,
        max_same_artist_window_15=3,
        label="ARTIST_WINDOW_5_RELAXED",
    ),
)


@dataclass(slots=True)
class RerankResult:
    items: list[RerankCandidate] = field(default_factory=list)
    relaxations: list[str] = field(default_factory=list)
    actual_familiar_count: int = 0


def _violates(
    candidate: RerankCandidate,
    chosen: Sequence[RerankCandidate],
    limits: DiversityLimits,
) -> bool:
    if candidate.artist_id is not None:
        recent_small = chosen[-limits.artist_window :]
        if sum(1 for item in recent_small if item.artist_id == candidate.artist_id) >= (
            MAX_SAME_ARTIST_WINDOW_5
        ):
            return True
        recent_large = chosen[-15:]
        if sum(1 for item in recent_large if item.artist_id == candidate.artist_id) >= (
            limits.max_same_artist_window_15
        ):
            return True

    if limits.enforce_source_concentration and candidate.seed_video_id is not None:
        tail = chosen[-MAX_CONSECUTIVE_SAME_SOURCE:]
        if len(tail) == MAX_CONSECUTIVE_SAME_SOURCE and all(
            item.seed_video_id == candidate.seed_video_id for item in tail
        ):
            return True

    return False


def _adjusted_score(
    candidate: RerankCandidate,
    chosen: Sequence[RerankCandidate],
    familiar_needed: bool,
) -> float:
    score = candidate.score
    recent = chosen[-5:]
    if candidate.artist_id is not None and any(
        item.artist_id == candidate.artist_id for item in recent
    ):
        score -= SAME_ARTIST_PENALTY
    if candidate.album_id is not None and any(
        item.album_id == candidate.album_id for item in recent
    ):
        score -= SAME_ALBUM_PENALTY
    if any(item.video_id == candidate.video_id for item in chosen):
        score -= RECENT_TRACK_PENALTY
    if candidate.seed_video_id is not None:
        same_source = sum(1 for item in recent if item.seed_video_id == candidate.seed_video_id)
        score -= SOURCE_CONCENTRATION_PENALTY * same_source / max(1, len(recent))
    if familiar_needed == candidate.familiar:
        score += QUOTA_BONUS
    return score


def rerank(
    candidates: Sequence[RerankCandidate],
    length: int,
    familiar_target: int,
) -> RerankResult:
    """Greedy selection honouring diversity limits and the familiar quota."""
    result = RerankResult()
    remaining = list(candidates)
    chosen: list[RerankCandidate] = []
    familiar_count = 0
    ladder_index = 0

    while len(chosen) < length and remaining:
        limits = RELAXATION_LADDER[ladder_index]
        familiar_needed = familiar_count < familiar_target

        allowed = [item for item in remaining if not _violates(item, chosen, limits)]
        if not allowed:
            if ladder_index + 1 < len(RELAXATION_LADDER):
                ladder_index += 1
                label = RELAXATION_LADDER[ladder_index].label
                if label and label not in result.relaxations:
                    result.relaxations.append(label)
                continue
            break

        best = max(allowed, key=lambda item: _adjusted_score(item, chosen, familiar_needed))
        remaining.remove(best)
        chosen.append(best)
        if best.familiar:
            familiar_count += 1

    result.items = _annotate_positions(chosen)
    result.actual_familiar_count = familiar_count
    return result


def _annotate_positions(chosen: list[RerankCandidate]) -> list[RerankCandidate]:
    """Attach the distinct-seed reason when the prefix is well spread."""
    prefix = chosen[:DISTINCT_SEED_PREFIX]
    seeds = {item.seed_video_id for item in prefix if item.seed_video_id}
    if prefix and len(seeds) / max(1, len(prefix)) < MIN_DISTINCT_SEEDS_RATIO:
        return [
            replace(item, reason_codes=(*item.reason_codes, "LOW_SEED_DIVERSITY"))
            if index < DISTINCT_SEED_PREFIX
            else item
            for index, item in enumerate(chosen)
        ]
    return chosen
