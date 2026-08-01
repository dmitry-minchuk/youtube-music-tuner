"""Sequence-level diversity reranking (docs/05 section 11).

Artist window rules live here and nowhere else — hard filters only decide
whether a single track is admissible at all. When the constraints would make
the queue shorter than requested they are relaxed in a fixed, deterministic
order, and every relaxation is reported as a reason code.

Selection is greedy over a window, in the spirit of the determinantal point
process re-rankers used for feed diversity: each pick is judged against the
last few chosen tracks rather than the whole prefix. It is also *stochastic* —
the next track is sampled from the top of the ranking instead of always
taking the argmax. Without that, an unchanged pool would reproduce exactly
the same wave every time, which is the single loudest source of repetition.
Sampling is seeded, so a generation remains reproducible from its stored
``random_seed``.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

MAX_SAME_ARTIST_WINDOW_5 = 1
MAX_SAME_ARTIST_WINDOW_15 = 2
MAX_CONSECUTIVE_SAME_SOURCE = 3
MIN_DISTINCT_SEEDS_RATIO = 0.25
DISTINCT_SEED_PREFIX = 20

# Whether a track can be embedded is only knowable by trying it, so open
# the queue with tracks that already played. Unproven ones still appear, just
# not in the first few slots where a silent skip looks like a broken player.
PROVEN_HEAD_BONUS = 0.6
PROVEN_HEAD_POSITIONS = 4

SAME_ARTIST_PENALTY = 0.35
SAME_ALBUM_PENALTY = 0.15
RECENT_TRACK_PENALTY = 0.25
SOURCE_CONCENTRATION_PENALTY = 0.20
QUOTA_BONUS = 0.30

# Comparison window for the diversity penalties. Eight sits inside the six to
# twelve range that windowed DPP re-rankers use in production feeds.
DIVERSITY_WINDOW = 8

# Stochastic selection: sample from this many leading candidates, with a
# softness that grows with temperature.
SELECTION_TOP_K = 12
SELECTION_TAU_MIN = 0.05
SELECTION_TAU_MAX = 0.22


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
    proven_playable: bool = False
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
    quota_pressure: float,
) -> float:
    score = candidate.score
    recent = chosen[-DIVERSITY_WINDOW:]
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
    # The quota is a target share, not a tie breaker: the nudge grows as the
    # remaining slots run out, the way calibrated re-rankers keep a feed's
    # composition on target instead of hoping the scores line up.
    if candidate.familiar:
        score += QUOTA_BONUS * quota_pressure
    else:
        score += QUOTA_BONUS * (1.0 - quota_pressure)
    if not candidate.proven_playable and len(chosen) < PROVEN_HEAD_POSITIONS:
        # Fades out after the opening positions so discovery is not punished.
        score -= PROVEN_HEAD_BONUS * (1 - len(chosen) / PROVEN_HEAD_POSITIONS)
    return score


def selection_temperature(temperature: int) -> float:
    """Softness of the sampling distribution, driven by the wave temperature."""
    position = max(0, min(100, temperature)) / 100
    return SELECTION_TAU_MIN + position * (SELECTION_TAU_MAX - SELECTION_TAU_MIN)


def _pick(
    scored: list[tuple[float, RerankCandidate]],
    rng: random.Random | None,
    tau: float,
) -> RerankCandidate:
    """Take the best candidate, or sample near the top when an rng is given."""
    scored.sort(key=lambda pair: (-pair[0], pair[1].video_id))
    if rng is None or len(scored) == 1:
        return scored[0][1]

    top = scored[:SELECTION_TOP_K]
    leader = top[0][0]
    weights = [math.exp(min(0.0, (value - leader)) / tau) for value, _ in top]
    total = sum(weights)
    if total <= 0.0:
        return top[0][1]

    threshold = rng.random() * total
    cumulative = 0.0
    for weight, (_, candidate) in zip(weights, top, strict=True):
        cumulative += weight
        if threshold <= cumulative:
            return candidate
    return top[-1][1]


def rerank(
    candidates: Sequence[RerankCandidate],
    length: int,
    familiar_target: int,
    *,
    rng: random.Random | None = None,
    temperature: int = 50,
) -> RerankResult:
    """Windowed selection honouring diversity limits and the familiar quota."""
    result = RerankResult()
    remaining = list(candidates)
    chosen: list[RerankCandidate] = []
    familiar_count = 0
    ladder_index = 0
    tau = selection_temperature(temperature)

    while len(chosen) < length and remaining:
        limits = RELAXATION_LADDER[ladder_index]
        slots_left = length - len(chosen)
        deficit = max(0, familiar_target - familiar_count)
        quota_pressure = min(1.0, deficit / slots_left) if slots_left else 0.0

        allowed = [item for item in remaining if not _violates(item, chosen, limits)]
        if not allowed:
            if ladder_index + 1 < len(RELAXATION_LADDER):
                ladder_index += 1
                label = RELAXATION_LADDER[ladder_index].label
                if label and label not in result.relaxations:
                    result.relaxations.append(label)
                continue
            break

        # Every remaining slot is owed to the familiar side: stop competing on
        # score and just take one, as long as any is still admissible.
        if deficit >= slots_left:
            owed = [item for item in allowed if item.familiar]
            if owed:
                allowed = owed

        scored = [(_adjusted_score(item, chosen, quota_pressure), item) for item in allowed]
        best = _pick(scored, rng, tau)
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
