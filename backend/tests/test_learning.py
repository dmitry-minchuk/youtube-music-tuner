"""Bootstrap, shadow training and safety gates (docs/05 sections 7-13)."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from app.jobs.model_train import collect_samples, run_model_training
from app.persistence.models import FeatureSnapshot, ModelSnapshot, PlaybackSession, utcnow
from app.recommender.features import FEATURE_DIMENSION, FEATURE_SCHEMA_VERSION, FeatureVector
from app.recommender.learning_state import (
    BOOTSTRAP_MIN_SESSIONS,
    CLEAN_BASELINE_SESSIONS,
    Phase,
    learning_status,
    may_activate,
    progress_label,
)
from app.recommender.linucb import LinUcbModel, train
from app.recommender.safety_gates import evaluate_snapshot


def add_session(
    db,
    index: int,
    *,
    reward: float,
    video_id: str | None = None,
    with_features: bool = True,
) -> str:
    session_id = f"sess-{index}"
    generation_id = f"gen-{index}"
    video = video_id or f"v-{index}"
    db.add(
        PlaybackSession(
            session_id=session_id,
            video_id=video,
            generation_id=generation_id,
            started_at=utcnow() - dt.timedelta(minutes=index),
            qualified=True,
            reward=reward,
            reward_version="reward-v1",
            classification_basis="RATIO",
            source="TUNER",
        )
    )
    if with_features:
        vector = FeatureVector(
            is_liked=1.0 if reward > 0 else 0.0,
            artist_affinity=0.5 + reward / 4,
            never_played=0.0 if reward > 0 else 1.0,
        )
        db.add(
            FeatureSnapshot(
                session_id=session_id,
                generation_id=generation_id,
                video_id=video,
                feature_schema_version=FEATURE_SCHEMA_VERSION,
                features_json=vector.to_dict(),
            )
        )
    db.flush()
    return session_id


def build_history(db, sessions: int, *, positive_ratio: float = 0.5, offset: int = 0) -> None:
    positives = int(sessions * positive_ratio)
    for index in range(sessions):
        add_session(db, offset + index, reward=0.6 if index < positives else -0.6)


# -- feature schema -------------------------------------------------------


def test_feature_vector_round_trips() -> None:
    vector = FeatureVector(is_liked=1.0, temperature=0.8)
    restored = FeatureVector.from_dict(vector.to_dict())
    assert restored == vector
    assert vector.to_array().shape == (FEATURE_DIMENSION,)


def test_feature_values_stay_normalised() -> None:
    from app.recommender.features import build_features

    vector = build_features(
        is_liked=True,
        artist_decayed_reward=5.0,
        seed_mean_reward=-5.0,
        distinct_seed_count=99,
        best_source_rank=1,
        plays_all=1000,
        last_played_at=utcnow() - dt.timedelta(days=4000),
        plays_7d=999,
        artist_plays_7d=999,
        skipped_recently=True,
        temperature=100,
        recent_session_reward=1.0,
        has_duration=True,
        observation_count=1000,
        now=utcnow(),
    )
    array = vector.to_array()
    assert np.all(array >= 0.0)
    assert np.all(array <= 1.0)


# -- LinUCB ---------------------------------------------------------------


def test_untrained_model_predicts_zero() -> None:
    model = LinUcbModel()
    x = FeatureVector().to_array()
    assert model.expected(x) == 0.0
    assert model.uncertainty(x) > 0


def test_updates_move_the_prediction_toward_the_reward() -> None:
    model = LinUcbModel()
    x = FeatureVector(is_liked=1.0).to_array()
    before = model.expected(x)
    for _ in range(20):
        model.update(x, 0.8)
    assert model.expected(x) > before


def test_uncertainty_shrinks_with_observations() -> None:
    model = LinUcbModel()
    x = FeatureVector(is_liked=1.0).to_array()
    first = model.uncertainty(x)
    for _ in range(30):
        model.update(x, 0.5)
    assert model.uncertainty(x) < first


def test_expected_is_clamped_to_the_quality_scale() -> None:
    model = LinUcbModel()
    x = FeatureVector(is_liked=1.0).to_array()
    for _ in range(200):
        model.update(x, 5.0)  # deliberately out of range
    assert -1.0 <= model.expected(x) <= 1.0


def test_model_survives_a_round_trip_through_bytes() -> None:
    model = train([(FeatureVector(is_liked=1.0).to_array(), 0.7)])
    restored = LinUcbModel.from_bytes(model.to_bytes(), FEATURE_SCHEMA_VERSION)
    x = FeatureVector(is_liked=1.0).to_array()
    assert restored.expected(x) == pytest.approx(model.expected(x))
    assert restored.update_count == model.update_count


def test_ucb_adds_the_exploration_bonus() -> None:
    model = LinUcbModel()
    x = FeatureVector(is_liked=1.0).to_array()
    assert model.ucb_score(x, 1.0) > model.ucb_score(x, 0.1)


# -- bootstrap thresholds -------------------------------------------------


def test_bootstrap_needs_all_four_thresholds(db_session) -> None:
    # Enough sessions, but every one of them positive.
    build_history(db_session, BOOTSTRAP_MIN_SESSIONS, positive_ratio=1.0)
    status = learning_status(db_session)
    assert status.qualified_sessions >= BOOTSTRAP_MIN_SESSIONS
    assert status.bootstrap_ready is False
    assert status.missing["negative"] > 0


def test_bootstrap_ready_when_all_thresholds_met(db_session) -> None:
    build_history(db_session, 40, positive_ratio=0.6)
    status = learning_status(db_session)
    assert status.bootstrap_ready is True
    assert status.phase is Phase.BASELINE  # no snapshot trained yet


def test_training_is_skipped_before_bootstrap(db_session) -> None:
    build_history(db_session, 10)
    result = run_model_training(db_session)
    assert result.status == "SKIPPED"
    assert result.skipped_reason == "BOOTSTRAP_NOT_READY"


def test_first_snapshot_after_bootstrap_is_shadow_only(db_session) -> None:
    build_history(db_session, 45, positive_ratio=0.6)
    result = run_model_training(db_session)
    assert result.status == "SHADOW"

    status = learning_status(db_session)
    assert status.phase is Phase.SHADOW
    assert status.serving_policy == "rule-score-v1"
    assert status.serving_model_id is None
    assert status.shadow_model_id == result.model_id


def test_active_is_impossible_before_the_clean_baseline(db_session) -> None:
    build_history(db_session, 99, positive_ratio=0.6)
    result = run_model_training(db_session)
    assert result.status == "SHADOW"
    assert may_activate(learning_status(db_session)) is False


def test_activation_becomes_possible_after_one_hundred_sessions(db_session) -> None:
    build_history(db_session, CLEAN_BASELINE_SESSIONS, positive_ratio=0.6)
    result = run_model_training(db_session)
    assert result.status == "ACTIVE"

    status = learning_status(db_session)
    assert status.phase is Phase.ACTIVE
    assert status.serving_policy == "linucb-v1"
    assert status.serving_model_id == result.model_id


def test_retraining_requires_fifteen_new_sessions(db_session) -> None:
    build_history(db_session, 45, positive_ratio=0.6)
    run_model_training(db_session)

    # Backdate so the 60 minute interval is not the blocking reason.
    snapshot = db_session.query(ModelSnapshot).one()
    snapshot.created_at = utcnow() - dt.timedelta(hours=2)
    db_session.flush()

    for index in range(100, 105):
        add_session(db_session, index, reward=0.5)
    assert run_model_training(db_session).skipped_reason == "NOT_ENOUGH_NEW_SESSIONS"

    for index in range(200, 215):
        add_session(db_session, index, reward=0.5)
    assert run_model_training(db_session).status in {"SHADOW", "ACTIVE"}


def test_training_only_uses_stored_selection_features(db_session) -> None:
    build_history(db_session, 20, positive_ratio=0.5)
    add_session(db_session, 900, reward=0.9, with_features=False)

    samples = collect_samples(db_session)
    assert len(samples) == 20  # the session without a snapshot is not trainable


# -- safety gates ---------------------------------------------------------


def test_non_finite_snapshot_is_rejected() -> None:
    model = LinUcbModel()
    assert model.a_matrix is not None
    model.a_matrix[0][0] = np.nan
    result = evaluate_snapshot(
        model,
        offline_mean_reward=1.0,
        current_mean_reward=0.0,
        top_candidate_artists=["a"],
        top_candidate_disliked=[False],
    )
    assert result.passed is False
    assert "NON_FINITE_PARAMETERS" in result.failures


def test_offline_regression_beyond_tolerance_is_rejected() -> None:
    result = evaluate_snapshot(
        LinUcbModel(),
        offline_mean_reward=0.10,
        current_mean_reward=0.20,
        top_candidate_artists=["a", "b"],
        top_candidate_disliked=[False, False],
    )
    assert "OFFLINE_REGRESSION" in result.failures


def test_small_offline_regression_within_tolerance_passes() -> None:
    artists = [f"artist{index}" for index in range(20)]
    result = evaluate_snapshot(
        LinUcbModel(),
        offline_mean_reward=0.19,
        current_mean_reward=0.20,
        top_candidate_artists=artists,
        top_candidate_disliked=[False] * 20,
    )
    assert result.passed is True


def test_concentration_is_not_judged_on_a_tiny_sample() -> None:
    """Two artists must not read as 40% concentration."""
    result = evaluate_snapshot(
        LinUcbModel(),
        offline_mean_reward=1.0,
        current_mean_reward=0.0,
        top_candidate_artists=["a", "b"],
        top_candidate_disliked=[False, False],
    )
    assert result.passed is True


def test_artist_concentration_above_forty_percent_is_rejected() -> None:
    artists = ["mono"] * 25 + [f"other{i}" for i in range(25)]
    result = evaluate_snapshot(
        LinUcbModel(),
        offline_mean_reward=1.0,
        current_mean_reward=0.0,
        top_candidate_artists=artists,
        top_candidate_disliked=[False] * 50,
    )
    assert "ARTIST_CONCENTRATION" in result.failures


def test_disliked_track_in_top_is_rejected() -> None:
    result = evaluate_snapshot(
        LinUcbModel(),
        offline_mean_reward=1.0,
        current_mean_reward=0.0,
        top_candidate_artists=["a", "b"],
        top_candidate_disliked=[False, True],
    )
    assert "DISLIKED_IN_TOP" in result.failures


def test_schema_mismatch_is_rejected() -> None:
    model = LinUcbModel(feature_schema_version="features-v0")
    result = evaluate_snapshot(
        model,
        offline_mean_reward=1.0,
        current_mean_reward=0.0,
        top_candidate_artists=["a"],
        top_candidate_disliked=[False],
    )
    assert "FEATURE_SCHEMA_MISMATCH" in result.failures


# -- UI labels ------------------------------------------------------------


def test_progress_label_never_over_promises(db_session) -> None:
    build_history(db_session, 5)
    assert progress_label(learning_status(db_session)).startswith("Collecting signal 5/40")

    build_history(db_session, 60, positive_ratio=0.6, offset=1000)
    label = progress_label(learning_status(db_session))
    assert "shadow" in label or "Collecting" in label
    assert "AI" not in label
