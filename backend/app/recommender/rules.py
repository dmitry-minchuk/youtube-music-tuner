"""Rule-based cold-start ranker ``rule-score-v1`` (docs/05 section 7).

The weighted sum is clamped to [0, 1] and mapped onto the same [-1, 1] scale
as the LinUCB exploitation component, so playlist quality gates use one
threshold in every phase:

    quality_expected = 2 * rule_score - 1

``quality_expected >= -0.20`` is therefore ``rule_score >= 0.40``.
"""

from __future__ import annotations

from dataclasses import dataclass

RULE_POLICY_VERSION = "rule-score-v1"

W_SOURCE_STRENGTH = 0.35
W_SEED_AFFINITY = 0.25
W_ARTIST_AFFINITY = 0.20
W_REDISCOVERY = 0.10
W_NOVELTY = 0.10

QUALITY_EXPECTED_FLOOR = -0.20

# Relative strength of each candidate source, normalised to [0, 1].
SOURCE_STRENGTH = {
    "LIKED": 1.0,
    "PLAYLIST": 0.75,
    "RELATED": 0.70,
    "RADIO": 0.60,
    "MOOD": 0.50,
}


@dataclass(frozen=True, slots=True)
class RuleFeatures:
    source_strength: float = 0.0
    seed_affinity: float = 0.0
    artist_affinity: float = 0.0
    rediscovery: float = 0.0
    novelty: float = 0.0
    fatigue_penalty: float = 0.0
    recent_skip_penalty: float = 0.0


def rule_score(features: RuleFeatures) -> float:
    """Weighted sum clamped to [0, 1]."""
    total = (
        W_SOURCE_STRENGTH * features.source_strength
        + W_SEED_AFFINITY * features.seed_affinity
        + W_ARTIST_AFFINITY * features.artist_affinity
        + W_REDISCOVERY * features.rediscovery
        + W_NOVELTY * features.novelty
        - features.fatigue_penalty
        - features.recent_skip_penalty
    )
    return max(0.0, min(1.0, total))


def rule_to_quality_expected(score: float) -> float:
    """Map a rule score onto the shared [-1, 1] quality scale."""
    return 2.0 * max(0.0, min(1.0, score)) - 1.0


def passes_quality_floor(quality_expected: float) -> bool:
    return quality_expected >= QUALITY_EXPECTED_FLOOR
