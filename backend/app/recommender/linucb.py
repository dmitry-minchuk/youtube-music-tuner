"""Shared linear UCB on NumPy (docs/05 section 8).

    theta       = A^-1 b
    expected    = theta^T x
    uncertainty = sqrt(x^T A^-1 x)
    ucb_score   = expected + alpha(temperature) * uncertainty

After a qualified session:  A += x x^T ,  b += reward * x
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import numpy as np

from app.recommender.features import FEATURE_DIMENSION, FEATURE_SCHEMA_VERSION

ALGORITHM = "linucb-v1"
L2_REGULARIZATION = 1.0


@dataclass(slots=True)
class LinUcbModel:
    dimension: int = FEATURE_DIMENSION
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    a_matrix: np.ndarray | None = None
    b_vector: np.ndarray | None = None
    update_count: int = 0

    def __post_init__(self) -> None:
        if self.a_matrix is None:
            self.a_matrix = L2_REGULARIZATION * np.eye(self.dimension, dtype=np.float64)
        if self.b_vector is None:
            self.b_vector = np.zeros(self.dimension, dtype=np.float64)

    # -- inference --------------------------------------------------------

    def theta(self) -> np.ndarray:
        assert self.a_matrix is not None and self.b_vector is not None
        return np.linalg.solve(self.a_matrix, self.b_vector)

    def expected(self, x: np.ndarray) -> float:
        """Exploitation component, clamped to the shared quality scale."""
        value = float(self.theta() @ x)
        return max(-1.0, min(1.0, value))

    def uncertainty(self, x: np.ndarray) -> float:
        assert self.a_matrix is not None
        inverse = np.linalg.inv(self.a_matrix)
        value = float(x @ inverse @ x)
        return float(np.sqrt(max(0.0, value)))

    def ucb_score(self, x: np.ndarray, alpha: float) -> float:
        return self.expected(x) + alpha * self.uncertainty(x)

    # -- training ---------------------------------------------------------

    def update(self, x: np.ndarray, reward: float) -> None:
        assert self.a_matrix is not None and self.b_vector is not None
        self.a_matrix = self.a_matrix + np.outer(x, x)
        self.b_vector = self.b_vector + reward * x
        self.update_count += 1

    def is_finite(self) -> bool:
        assert self.a_matrix is not None and self.b_vector is not None
        return bool(np.all(np.isfinite(self.a_matrix)) and np.all(np.isfinite(self.b_vector)))

    # -- persistence ------------------------------------------------------

    def to_bytes(self) -> bytes:
        assert self.a_matrix is not None and self.b_vector is not None
        buffer = io.BytesIO()
        np.savez(
            buffer,
            a=self.a_matrix,
            b=self.b_vector,
            update_count=np.array([self.update_count]),
        )
        return buffer.getvalue()

    @classmethod
    def from_bytes(cls, payload: bytes, feature_schema_version: str) -> LinUcbModel:
        with np.load(io.BytesIO(payload)) as data:
            a_matrix = data["a"]
            b_vector = data["b"]
            update_count = int(data["update_count"][0])
        return cls(
            dimension=a_matrix.shape[0],
            feature_schema_version=feature_schema_version,
            a_matrix=a_matrix,
            b_vector=b_vector,
            update_count=update_count,
        )


def train(samples: list[tuple[np.ndarray, float]]) -> LinUcbModel:
    """Fit a fresh model over stored (features at selection time, reward)."""
    model = LinUcbModel()
    for features, reward in samples:
        model.update(features, reward)
    return model


def mean_expected_reward(model: LinUcbModel, samples: list[tuple[np.ndarray, float]]) -> float:
    """Offline replay proxy: how well the model orders what actually happened."""
    if not samples:
        return 0.0
    return float(np.mean([model.expected(features) * reward for features, reward in samples]))
