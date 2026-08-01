from __future__ import annotations

from fastapi.testclient import TestClient


def test_live_does_not_touch_database(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_applied_migrations(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["migrations"] == "ok"
    assert body["checks"]["dataDirWritable"] is True


def test_ready_is_not_ready_without_migrations(data_dir, monkeypatch) -> None:
    from app.main import create_app
    from app.settings import get_settings

    app = create_app(get_settings())
    with TestClient(app, base_url="http://127.0.0.1:43127") as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    csp = response.headers["Content-Security-Policy"]
    assert "frame-src https://www.youtube.com" in csp
    assert "unsafe-eval" not in csp


def test_csp_has_no_invalid_ipv6_source(client: TestClient) -> None:
    """Bracketed IPv6 literals are not valid CSP host-sources."""
    csp = client.get("/health/live").headers["Content-Security-Policy"]
    assert "[::1]" not in csp
    assert "'self'" in csp
