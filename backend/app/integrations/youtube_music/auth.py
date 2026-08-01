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


@dataclass(frozen=True, slots=True)
class OAuthStatus:
    connected: bool
    client_configured: bool
    token_present: bool
    reason: str


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


def read_oauth_status(settings: Settings) -> OAuthStatus:
    client = _readable_json(settings.client_secret_file)
    token = _readable_json(settings.oauth_file)
    client_configured = client is not None and all(
        client.get(field) for field in REQUIRED_CLIENT_FIELDS
    )
    token_present = token is not None and bool(token.get("refresh_token"))

    if not client_configured:
        reason = "client credentials not imported"
    elif not token_present:
        reason = "device flow not completed"
    else:
        reason = "connected"

    return OAuthStatus(
        connected=client_configured and token_present,
        client_configured=client_configured,
        token_present=token_present,
        reason=reason,
    )
