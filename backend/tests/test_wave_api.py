"""Wave endpoint contract details (docs/08 section 4)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.persistence.models import QueueGeneration


def _generation(**overrides) -> QueueGeneration:
    defaults = dict(
        generation_id="gen-1",
        queue_id="queue-1",
        temperature=50,
        mood="ANY",
        serving_policy="rule-score-v1",
        random_seed=1,
        relaxations_json={"codes": []},
    )
    defaults.update(overrides)
    return QueueGeneration(**defaults)


def test_restored_shadow_generation_is_not_reported_as_baseline(
    client: TestClient, db_session
) -> None:
    """A shadow model was watching: the reload path must say SHADOW, not
    upgrade the phase to BASELINE (docs/08 section 4)."""
    db_session.add(_generation(shadow_model_id="shadow-model"))
    db_session.commit()

    response = client.get("/api/v1/waves/queue-1")
    assert response.status_code == 200
    assert response.json()["ranking"]["phase"] == "SHADOW"


def test_restored_active_generation_reports_active(client: TestClient, db_session) -> None:
    db_session.add(
        _generation(
            serving_policy="linucb-v1",
            serving_model_id="active-model",
            quality_score_source="LINUCB_EXPECTED",
        )
    )
    db_session.commit()

    response = client.get("/api/v1/waves/queue-1")
    assert response.status_code == 200
    assert response.json()["ranking"]["phase"] == "ACTIVE"


def test_restored_baseline_generation_reports_baseline(client: TestClient, db_session) -> None:
    db_session.add(_generation())
    db_session.commit()

    response = client.get("/api/v1/waves/queue-1")
    assert response.status_code == 200
    assert response.json()["ranking"]["phase"] == "BASELINE"
