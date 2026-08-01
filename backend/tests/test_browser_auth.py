"""Browser (cookie) authentication (docs/03 section 2).

YouTube Music answers HTTP 400 to Bearer tokens issued to a self-made OAuth
client, so browser headers are the path that actually works and must take
precedence over any stored OAuth token.
"""

from __future__ import annotations

import json

import pytest

from app.integrations.youtube_music.auth import (
    browser_auth_present,
    parse_browser_headers,
    read_oauth_status,
)
from app.settings import Settings

CHROME_PASTE = """
:authority: music.youtube.com
:method: POST
accept: */*
accept-language: en-US,en;q=0.9
authorization: SAPISIDHASH 1700000000_abc
content-type: application/json
cookie: VISITOR_INFO1_LIVE=abc; SID=def; HSID=ghi; SAPISID=jkl
origin: https://music.youtube.com
user-agent: Mozilla/5.0
x-goog-authuser: 0
"""


def make_settings(tmp_path) -> Settings:
    settings = Settings(data_dir=tmp_path)
    settings.secrets_dir.mkdir(parents=True, exist_ok=True)
    return settings


def test_headers_pasted_from_chrome_are_parsed() -> None:
    headers = parse_browser_headers(CHROME_PASTE)
    assert "SAPISID=jkl" in headers["cookie"]
    assert headers["user-agent"] == "Mozilla/5.0"
    # HTTP/2 pseudo-headers must not leak into the stored set.
    assert not any(name.startswith(":") for name in headers)


def test_header_names_are_case_insensitive() -> None:
    headers = parse_browser_headers("Cookie: SID=1\nUser-Agent: Firefox")
    assert headers["cookie"] == "SID=1"
    assert headers["user-agent"] == "Firefox"


def test_blank_lines_and_junk_are_ignored() -> None:
    headers = parse_browser_headers("\n\ncookie: SID=1\n\nnot a header line\n")
    assert headers == {"cookie": "SID=1"}


def test_missing_cookie_is_rejected_with_a_useful_message() -> None:
    with pytest.raises(ValueError) as excinfo:
        parse_browser_headers("user-agent: Mozilla/5.0")
    assert "cookie" in str(excinfo.value)
    assert "youtubei" in str(excinfo.value)


def test_status_reports_browser_method(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings.browser_auth_file.write_text(json.dumps({"Cookie": "SID=1"}))

    status = read_oauth_status(settings)
    assert status.connected is True
    assert status.method == "BROWSER"
    assert browser_auth_present(settings) is True


def test_browser_headers_win_over_a_stored_oauth_token(tmp_path) -> None:
    """A leftover OAuth token must not shadow working cookie auth."""
    settings = make_settings(tmp_path)
    settings.client_secret_file.write_text(json.dumps({"client_id": "a", "client_secret": "b"}))
    settings.oauth_file.write_text(json.dumps({"refresh_token": "stale"}))
    settings.browser_auth_file.write_text(json.dumps({"Cookie": "SID=1"}))

    assert read_oauth_status(settings).method == "BROWSER"


def test_without_any_credentials_the_reason_mentions_both_paths(tmp_path) -> None:
    status = read_oauth_status(make_settings(tmp_path))
    assert status.connected is False
    assert status.method == "NONE"
    assert "browser headers" in status.reason


def test_adapter_prefers_the_browser_file(tmp_path, monkeypatch) -> None:
    from app.integrations.youtube_music.adapter import YouTubeMusicAdapter

    settings = make_settings(tmp_path)
    settings.browser_auth_file.write_text(json.dumps({"Cookie": "SID=1"}))

    built: dict[str, object] = {}

    class FakeYTMusic:
        def __init__(self, auth=None, **kwargs) -> None:
            built["auth"] = auth
            built["kwargs"] = kwargs

    monkeypatch.setattr("ytmusicapi.YTMusic", FakeYTMusic)

    adapter = YouTubeMusicAdapter(settings)
    assert adapter.client is not None

    assert built["auth"] == str(settings.browser_auth_file)
    # Cookie auth needs no OAuth credentials object.
    assert "oauth_credentials" not in built["kwargs"]


def test_adapter_without_credentials_raises_auth_error(tmp_path) -> None:
    from app.integrations.youtube_music.adapter import YouTubeMusicAdapter
    from app.integrations.youtube_music.errors import AuthError

    adapter = YouTubeMusicAdapter(make_settings(tmp_path))
    with pytest.raises(AuthError):
        _ = adapter.client
