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
