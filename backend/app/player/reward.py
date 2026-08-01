"""Reward formula ``reward-v1`` (docs/05 section 6).

Raw weights are combined, then squashed with ``tanh(raw / 4)`` so that like,
replay and completion stay distinguishable instead of collapsing to +1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

REWARD_VERSION = "reward-v1"

# Raw weights.
W_LIKE = 5.0
W_DISLIKE = -8.0
W_REPLAY = 3.0
W_COMPLETION = 2.0
W_PARTIAL_LISTEN = 1.0
W_SEEK_BACK = 0.5
W_SEEK_BACK_CAP = 1.0
W_EARLY_SKIP = -3.0
W_MID_SKIP = -1.0
W_EARLY_SKIP_ABSOLUTE = -2.0
W_MID_SKIP_ABSOLUTE = -0.5

# Reachable bounds of the implicit sum.
IMPLICIT_MIN = -3.0
IMPLICIT_MAX = 6.0

# Explicit like keeps a little implicit gradient on top.
LIKE_IMPLICIT_BONUS_CAP = 2.0

TANH_SCALE = 4.0


@dataclass(frozen=True, slots=True)
class RewardInputs:
    explicit_rating: str | None = None
    completed: bool = False
    partial_listen: bool = False
    replayed: bool = False
    seek_backward_count: int = 0
    early_skip: bool = False
    mid_skip: bool = False
    absolute_basis: bool = False


def implicit_raw(inputs: RewardInputs) -> float:
    """Sum the applicable implicit weights, clamped to the reachable range."""
    total = 0.0

    # Completion dominates partial listen; they never stack.
    if inputs.completed:
        total += W_COMPLETION
    elif inputs.partial_listen:
        total += W_PARTIAL_LISTEN

    if inputs.replayed:
        total += W_REPLAY

    if inputs.seek_backward_count > 0:
        total += min(W_SEEK_BACK * inputs.seek_backward_count, W_SEEK_BACK_CAP)

    if inputs.early_skip:
        total += W_EARLY_SKIP_ABSOLUTE if inputs.absolute_basis else W_EARLY_SKIP
    elif inputs.mid_skip:
        total += W_MID_SKIP_ABSOLUTE if inputs.absolute_basis else W_MID_SKIP

    return max(IMPLICIT_MIN, min(IMPLICIT_MAX, total))


def raw_reward(inputs: RewardInputs) -> float:
    implicit = implicit_raw(inputs)

    if inputs.explicit_rating == "DISLIKE":
        return W_DISLIKE
    if inputs.explicit_rating == "LIKE":
        return W_LIKE + max(0.0, min(LIKE_IMPLICIT_BONUS_CAP, implicit))
    return implicit


def compute_reward(inputs: RewardInputs) -> float:
    """Normalised reward in the open interval (-1, 1)."""
    return math.tanh(raw_reward(inputs) / TANH_SCALE)
