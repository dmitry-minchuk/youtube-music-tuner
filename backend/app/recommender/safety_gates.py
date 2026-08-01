"""Model activation safety gates (docs/05 section 13)."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from app.recommender.features import FEATURE_SCHEMA_VERSION
from app.recommender.linucb import LinUcbModel

OFFLINE_REGRESSION_TOLERANCE = 0.03
MAX_SINGLE_ARTIST_SHARE_TOP_50 = 0.40
TOP_N = 50
# A share is meaningless on a handful of rows: two distinct artists would
# already read as 50%. Only judge concentration once the sample is real.
MIN_ARTISTS_FOR_CONCENTRATION = 10


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    failures: tuple[str, ...] = field(default=())

    @property
    def reason(self) -> str | None:
        return self.failures[0] if self.failures else None


def evaluate_snapshot(
    model: LinUcbModel,
    *,
    offline_mean_reward: float,
    current_mean_reward: float,
    top_candidate_artists: list[str | None],
    top_candidate_disliked: list[bool],
) -> GateResult:
    failures: list[str] = []

    if not model.is_finite():
        failures.append("NON_FINITE_PARAMETERS")

    if model.feature_schema_version != FEATURE_SCHEMA_VERSION:
        failures.append("FEATURE_SCHEMA_MISMATCH")

    if offline_mean_reward < current_mean_reward - OFFLINE_REGRESSION_TOLERANCE:
        failures.append("OFFLINE_REGRESSION")

    top_artists = [artist for artist in top_candidate_artists[:TOP_N] if artist]
    if len(top_artists) >= MIN_ARTISTS_FOR_CONCENTRATION:
        most_common = Counter(top_artists).most_common(1)[0][1]
        if most_common / len(top_artists) > MAX_SINGLE_ARTIST_SHARE_TOP_50:
            failures.append("ARTIST_CONCENTRATION")

    if any(top_candidate_disliked[:TOP_N]):
        failures.append("DISLIKED_IN_TOP")

    return GateResult(passed=not failures, failures=tuple(failures))


def has_non_finite(values: np.ndarray) -> bool:
    return bool(np.any(~np.isfinite(values)))
