"""User settings (docs/07 section 7)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_defaults_are_returned_before_anything_is_saved(client: TestClient) -> None:
    body = client.get("/api/v1/settings").json()
    # Nothing interrupts playback unless the owner asks for it.
    assert body["pauseOnHidden"] is False
    assert body["defaultTemperature"] == 50
    assert body["defaultMood"] == "ANY"


def test_a_setting_can_be_turned_on_and_persists(authed_client: TestClient) -> None:
    updated = authed_client.patch("/api/v1/settings", json={"pauseOnHidden": True}).json()
    assert updated["pauseOnHidden"] is True
    assert authed_client.get("/api/v1/settings").json()["pauseOnHidden"] is True


def test_partial_patch_leaves_other_values_alone(authed_client: TestClient) -> None:
    authed_client.patch("/api/v1/settings", json={"pauseOnHidden": True})
    authed_client.patch("/api/v1/settings", json={"defaultTemperature": 80})
    body = authed_client.get("/api/v1/settings").json()
    assert body["pauseOnHidden"] is True
    assert body["defaultTemperature"] == 80


def test_out_of_range_values_are_rejected(authed_client: TestClient) -> None:
    assert (
        authed_client.patch("/api/v1/settings", json={"defaultTemperature": 300}).status_code == 400
    )


def test_settings_changes_need_the_csrf_token(client: TestClient) -> None:
    assert client.patch("/api/v1/settings", json={"pauseOnHidden": False}).status_code == 403
