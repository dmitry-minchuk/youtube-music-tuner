"""Connecting from the UI (docs/03 section 2).

The paste replaces a terminal command, so it must keep every guarantee the
CLI had: guarded like any mutation, verified against YouTube Music before
being called a success, stored 0600, and never echoed back.
"""

from __future__ import annotations

import json
import stat

from fastapi.testclient import TestClient

from app.integrations.youtube_music.errors import AuthError
from app.settings import get_settings

HEADERS_PASTE = (
    "accept: */*\n"
    "cookie: VISITOR_INFO1_LIVE=abc; SID=def; __Secure-3PAPISID=mno\n"
    "user-agent: Mozilla/5.0\n"
    "origin: https://music.youtube.com\n"
)


def test_paste_connects_and_reports_what_it_saw(authed_client: TestClient, fake_catalog) -> None:
    from tests.fakes import track

    fake_catalog.liked = [track("v1", "One"), track("v2", "Two")]

    response = authed_client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is True
    assert body["method"] == "BROWSER"
    assert body["likedTracksVisible"] == 2


def test_stored_file_is_private_and_not_echoed(authed_client: TestClient) -> None:
    response = authed_client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})
    assert response.status_code == 200
    # The cookie must not come back in the response.
    assert "__Secure-3PAPISID" not in response.text

    path = get_settings().browser_auth_file
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    stored = json.loads(path.read_text())
    assert any(key.lower() == "cookie" for key in stored)


def test_authorization_is_derived_so_ytmusicapi_sees_browser_auth(
    authed_client: TestClient,
) -> None:
    authed_client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})
    stored = json.loads(get_settings().browser_auth_file.read_text())
    authorization = next(value for key, value in stored.items() if key.lower() == "authorization")
    assert "SAPISIDHASH" in authorization


def test_paste_without_a_cookie_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/v1/auth/browser-headers", json={"headers": "user-agent: Mozilla/5.0"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert not get_settings().browser_auth_file.exists()


def test_rejected_credentials_are_not_kept(authed_client: TestClient, fake_catalog) -> None:
    """A paste that YouTube Music refuses must not leave a broken file behind."""

    def refuse(limit=None, **_kwargs):
        raise AuthError("nope")

    fake_catalog.liked_tracks = refuse

    response = authed_client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "YTM_AUTH_REQUIRED"
    assert not get_settings().browser_auth_file.exists()


def test_connecting_requires_the_csrf_token(client: TestClient) -> None:
    response = client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})
    assert response.status_code == 403


def test_foreign_origin_cannot_connect(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/v1/auth/browser-headers",
        headers={"Origin": "http://evil.example.com"},
        json={"headers": HEADERS_PASTE},
    )
    assert response.status_code == 403


def test_oversized_paste_is_rejected(authed_client: TestClient) -> None:
    response = authed_client.post(
        "/api/v1/auth/browser-headers", json={"headers": "cookie: " + "x" * 40_000}
    )
    assert response.status_code == 400


def test_forget_removes_the_stored_cookies(authed_client: TestClient) -> None:
    authed_client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})
    assert get_settings().browser_auth_file.is_file()

    response = authed_client.post("/api/v1/auth/forget-browser-headers")
    assert response.status_code == 200
    assert response.json()["removed"] is True
    assert not get_settings().browser_auth_file.exists()


def test_disconnect_also_drops_browser_cookies(authed_client: TestClient) -> None:
    authed_client.post("/api/v1/auth/browser-headers", json={"headers": HEADERS_PASTE})

    response = authed_client.post("/api/v1/auth/disconnect")
    assert response.status_code == 200
    assert response.json()["cookiesRemoved"] is True
    assert not get_settings().browser_auth_file.exists()
