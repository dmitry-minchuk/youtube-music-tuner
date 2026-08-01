"""Feature schema v1 (docs/05 section 5).

A small fixed vector keeps LinUCB cheap and its matrix well conditioned.
Every value is normalised to [0, 1] (or [-1, 1] for reward-like signals) so
no single feature dominates the regularised solution.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

import numpy as np

FEATURE_SCHEMA_VERSION = "features-v1"

FEATURE_NAMES: tuple[str, ...] = (
    "bias",
    "is_liked",
    "artist_affinity",
    "seed_reward",
    "seed_support",
    "source_rank",
    "never_played",
    "days_since_play",
    "plays_7d",
    "artist_exposure_7d",
    "recent_skip",
    "rediscovery",
    "temperature",
    "recent_reward",
    "has_duration",
    "observation_confidence",
)

FEATURE_DIMENSION = len(FEATURE_NAMES)


def _unit(value: float) -> float:
    return max(0.0, min(1.0, value))


@dataclass(frozen=True, slots=True)
class FeatureVector:
    bias: float = 1.0
    is_liked: float = 0.0
    artist_affinity: float = 0.0
    seed_reward: float = 0.0
    seed_support: float = 0.0
    source_rank: float = 0.0
    never_played: float = 0.0
    days_since_play: float = 0.0
    plays_7d: float = 0.0
    artist_exposure_7d: float = 0.0
    recent_skip: float = 0.0
    rediscovery: float = 0.0
    temperature: float = 0.5
    recent_reward: float = 0.0
    has_duration: float = 0.0
    observation_confidence: float = 0.0

    def to_array(self) -> np.ndarray:
        return np.array([getattr(self, name) for name in FEATURE_NAMES], dtype=np.float64)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, float]) -> FeatureVector:
        known = {name: float(payload.get(name, 0.0)) for name in FEATURE_NAMES}
        return cls(**known)


def build_features(
    *,
    is_liked: bool,
    artist_decayed_reward: float,
    seed_mean_reward: float,
    distinct_seed_count: int,
    best_source_rank: int,
    plays_all: int,
    last_played_at: dt.datetime | None,
    plays_7d: int,
    artist_plays_7d: int,
    skipped_recently: bool,
    temperature: int,
    recent_session_reward: float,
    has_duration: bool,
    observation_count: int,
    now: dt.datetime,
) -> FeatureVector:
    days_since = 1.0
    if last_played_at is not None:
        days_since = _unit((now - last_played_at).days / 365)

    return FeatureVector(
        is_liked=1.0 if is_liked else 0.0,
        artist_affinity=_unit((artist_decayed_reward + 1) / 2),
        seed_reward=_unit((seed_mean_reward + 1) / 2),
        seed_support=_unit(distinct_seed_count / 5),
        # Rank 1 is the strongest signal; decay it smoothly.
        source_rank=_unit(1.0 / (1.0 + max(0, best_source_rank - 1) * 0.2)),
        never_played=1.0 if plays_all == 0 else 0.0,
        days_since_play=days_since,
        plays_7d=_unit(plays_7d / 10),
        artist_exposure_7d=_unit(artist_plays_7d / 20),
        recent_skip=1.0 if skipped_recently else 0.0,
        rediscovery=1.0 if (plays_all > 0 and days_since >= 60 / 365) else 0.0,
        temperature=_unit(temperature / 100),
        recent_reward=_unit((recent_session_reward + 1) / 2),
        has_duration=1.0 if has_duration else 0.0,
        observation_confidence=_unit(observation_count / 20),
    )
