"""Host, Origin and CSRF guards (docs/11 section 4)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_unknown_host_is_rejected_as_dns_rebinding(client: TestClient) -> None:
    response = client.get("/health/live", headers={"Host": "evil.example.com"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_REQUEST"


def test_loopback_hosts_are_allowed(client: TestClient) -> None:
    for host in ("127.0.0.1:43127", "localhost:43127", "localhost"):
        assert client.get("/health/live", headers={"Host": host}).status_code == 200


def test_mutation_without_session_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/auth/disconnect")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN_REQUEST"


def test_session_endpoint_issues_cookie_and_token(client: TestClient) -> None:
    response = client.get("/api/v1/session")
    assert response.status_code == 200
    body = response.json()
    assert body["headerName"] == "X-CSRF-Token"
    assert len(body["csrfToken"]) > 20
    assert "tuner_session" in response.cookies


def test_mutation_with_foreign_origin_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/v1/auth/disconnect",
        headers={"Origin": "http://evil.example.com"},
    )
    assert response.status_code == 403


def test_csrf_token_must_match_session(client: TestClient) -> None:
    client.get("/api/v1/session")
    response = client.post("/api/v1/auth/disconnect", headers={"X-CSRF-Token": "wrong-token"})
    assert response.status_code == 403


def test_beacon_path_accepts_session_cookie_without_csrf_header(client: TestClient) -> None:
    """sendBeacon cannot set headers; SameSite=Strict cookie is the guard."""
    client.get("/api/v1/session")  # sets the cookie, header deliberately unused
    response = client.post(
        "/api/v1/telemetry/events:beacon",
        json={
            "schemaVersion": 1,
            "events": [
                {
                    "clientEventId": "beacon-1",
                    "sessionId": "s-beacon",
                    "sequenceNo": 1,
                    "videoId": "v1",
                    "type": "page_closing",
                    "occurredAt": "2026-08-01T18:42:10.123Z",
                    "monotonicMs": 1000,
                    "payload": {},
                }
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["accepted"] == 1


def test_beacon_path_still_requires_a_session(client: TestClient) -> None:
    response = client.post(
        "/api/v1/telemetry/events:beacon", json={"schemaVersion": 1, "events": []}
    )
    assert response.status_code == 403


def test_beacon_path_rejects_a_foreign_origin(client: TestClient) -> None:
    client.get("/api/v1/session")
    response = client.post(
        "/api/v1/telemetry/events:beacon",
        headers={"Origin": "http://evil.example.com"},
        json={"schemaVersion": 1, "events": []},
    )
    assert response.status_code == 403
