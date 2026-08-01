"""OAuth file handling (docs/03 section 2, docs/10 sections 2 and 4).

Secrets live in files under ``<data_dir>/secrets`` with mode 0600. They are
never returned to the frontend, never logged, and never read from the
environment.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.settings import Settings

SECRET_FILE_MODE = 0o600
SECRET_DIR_MODE = 0o700
REQUIRED_CLIENT_FIELDS = ("client_id", "client_secret")


REQUIRED_BROWSER_HEADERS = ("cookie",)


@dataclass(frozen=True, slots=True)
class OAuthStatus:
    connected: bool
    client_configured: bool
    token_present: bool
    reason: str
    method: str = "NONE"  # BROWSER | OAUTH | NONE


def ensure_secrets_dir(settings: Settings) -> Path:
    settings.secrets_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(settings.secrets_dir, SECRET_DIR_MODE)
    return settings.secrets_dir


def write_secret_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive permissions before any content is written.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    os.chmod(path, SECRET_FILE_MODE)


def _readable_json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def normalize_client_payload(raw: dict[str, object]) -> dict[str, object]:
    """Accept both the bare dict and the Google Console ``{"installed": {...}}`` shape."""
    payload = raw
    for wrapper in ("installed", "web"):
        nested = raw.get(wrapper)
        if isinstance(nested, dict):
            payload = nested
            break
    missing = [field for field in REQUIRED_CLIENT_FIELDS if not payload.get(field)]
    if missing:
        raise ValueError(f"client JSON is missing required fields: {', '.join(missing)}")
    return {field: payload[field] for field in REQUIRED_CLIENT_FIELDS}


def has_insecure_mode(path: Path) -> bool:
    if not path.is_file():
        return False
    mode = stat.S_IMODE(path.stat().st_mode)
    return bool(mode & 0o077)


def parse_browser_headers(raw: str) -> dict[str, str]:
    """Parse request headers copied from the browser's network tab.

    Accepts the ``Name: value`` block that Chrome and Firefox produce under
    "Copy request headers", and tolerates the ``name: value`` casing plus
    blank lines. Only a Cookie header is strictly required.
    """
    headers: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        # Skip HTTP/2 pseudo-headers such as :authority.
        if not name or name.startswith(":") or not value:
            continue
        headers[name] = value

    missing = [field for field in REQUIRED_BROWSER_HEADERS if field not in headers]
    if missing:
        raise ValueError(
            "pasted headers are missing: " + ", ".join(missing) + "; copy the request headers "
            "of a POST to music.youtube.com/youtubei/v1/..."
        )
    return headers


def complete_browser_headers(headers: dict[str, str]) -> dict[str, str]:
    """Fill in what a copy-paste usually loses.

    ytmusicapi classifies a header set as browser auth only if it carries an
    ``authorization`` value containing ``SAPISIDHASH``; without it the file is
    mistaken for an OAuth token. It also insists on ``x-goog-authuser``.
    Browsers send both, but they are easy to miss when copying, so derive the
    authorization from the cookie and default the account index to the primary
    one. ytmusicapi recomputes the authorization on every request, so a stale
    timestamp here is harmless.
    """
    enriched = dict(headers)
    # 0 is the signed-in primary account; multi-account users copy their own.
    enriched.setdefault("x-goog-authuser", "0")
    existing = enriched.get("authorization", "")
    if "SAPISIDHASH" in existing:
        return enriched

    cookie = enriched.get("cookie", "")
    origin = enriched.get("origin") or enriched.get("x-origin") or "https://music.youtube.com"

    from ytmusicapi.helpers import get_authorization, sapisid_from_cookie

    try:
        sapisid = sapisid_from_cookie(cookie)
    except KeyError as exc:
        raise ValueError(
            "the pasted cookie has no __Secure-3PAPISID; copy the headers again "
            "from a signed-in music.youtube.com tab"
        ) from exc

    enriched["authorization"] = get_authorization(f"{sapisid} {origin}")
    enriched.setdefault("origin", origin)
    return enriched


def browser_auth_present(settings: Settings) -> bool:
    data = _readable_json(settings.browser_auth_file)
    return bool(data and data.get("Cookie") or data and data.get("cookie"))


def read_oauth_status(settings: Settings) -> OAuthStatus:
    """Report how (and whether) the app can talk to YouTube Music.

    Browser headers take precedence: YouTube Music rejects Bearer tokens
    issued to self-made OAuth clients, so cookie auth is the working path
    (docs/03 section 2).
    """
    if browser_auth_present(settings):
        return OAuthStatus(
            connected=True,
            client_configured=True,
            token_present=True,
            reason="connected via browser headers",
            method="BROWSER",
        )

    client = _readable_json(settings.client_secret_file)
    token = _readable_json(settings.oauth_file)
    client_configured = client is not None and all(
        client.get(field) for field in REQUIRED_CLIENT_FIELDS
    )
    token_present = token is not None and bool(token.get("refresh_token"))

    if not client_configured:
        reason = "no browser headers and no OAuth client imported"
    elif not token_present:
        reason = "device flow not completed"
    else:
        reason = "connected via OAuth"

    return OAuthStatus(
        connected=client_configured and token_present,
        client_configured=client_configured,
        token_present=token_present,
        reason=reason,
        method="OAUTH" if (client_configured and token_present) else "NONE",
    )
