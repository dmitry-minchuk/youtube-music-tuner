"""Operator CLI: ``python -m app.cli <command>``.

The web UI never accepts or displays the client secret (docs/03 section 2),
so credential import and the OAuth device flow live here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from app.integrations.youtube_music.auth import (
    SECRET_FILE_MODE,
    ensure_secrets_dir,
    normalize_client_payload,
    read_oauth_status,
    write_secret_file,
)
from app.settings import Settings, get_settings


def _print(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_credentials_import(args: argparse.Namespace, settings: Settings) -> int:
    source = args.source
    raw_text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    try:
        payload = normalize_client_payload(json.loads(raw_text))
    except (json.JSONDecodeError, ValueError) as exc:
        _print({"status": "error", "message": str(exc)})
        return 2

    ensure_secrets_dir(settings)
    write_secret_file(settings.client_secret_file, payload)
    _print(
        {
            "status": "ok",
            "path": str(settings.client_secret_file),
            "mode": oct(SECRET_FILE_MODE),
            "next": "python -m app.cli auth",
        }
    )
    return 0


def cmd_auth(_: argparse.Namespace, settings: Settings) -> int:
    """Run the Google device flow and store a refreshable token."""
    if not settings.client_secret_file.is_file():
        _print(
            {
                "status": "error",
                "message": "client credentials missing; run 'credentials import' first",
            }
        )
        return 2

    client = json.loads(settings.client_secret_file.read_text(encoding="utf-8"))
    from ytmusicapi import setup_oauth

    ensure_secrets_dir(settings)
    token = setup_oauth(
        client_id=str(client["client_id"]),
        client_secret=str(client["client_secret"]),
        filepath=str(settings.oauth_file),
        open_browser=False,
    )
    if token is None:
        _print({"status": "error", "message": "device flow did not complete"})
        return 1

    settings.oauth_file.chmod(SECRET_FILE_MODE)

    # Confirm the account without printing any token material.
    from app.integrations.youtube_music.adapter import YouTubeMusicAdapter

    adapter = YouTubeMusicAdapter(settings)
    account = adapter.account()
    _print({"status": "ok", "account": account.name, "handle": account.channel_handle})
    return 0


def cmd_status(_: argparse.Namespace, settings: Settings) -> int:
    status = read_oauth_status(settings)
    _print(
        {
            "connected": status.connected,
            "clientConfigured": status.client_configured,
            "tokenPresent": status.token_present,
            "reason": status.reason,
            "dataDir": str(settings.data_dir),
        }
    )
    return 0


def cmd_backup(_: argparse.Namespace, settings: Settings) -> int:
    """Consistent VACUUM INTO copy plus a checksummed manifest."""
    if not settings.database_path.is_file():
        _print({"status": "error", "message": "database does not exist yet"})
        return 2

    stamp = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    target_dir = settings.backups_dir / stamp
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "tuner.db"

    source = sqlite3.connect(settings.database_path)
    try:
        source.execute("VACUUM INTO ?", (str(target),))
    finally:
        source.close()

    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    verifier = sqlite3.connect(target)
    try:
        integrity = verifier.execute("PRAGMA integrity_check").fetchone()[0]
        schema_version = verifier.execute(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
    finally:
        verifier.close()

    manifest = {
        "createdAt": dt.datetime.now(dt.UTC).isoformat(),
        "appVersion": "0.1.0",
        "schemaVersion": schema_version[0] if schema_version else None,
        "sha256": digest,
        "sizeBytes": target.stat().st_size,
        "integrityCheck": integrity,
        "containsSecrets": False,
    }
    (target_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if integrity != "ok":
        _print({"status": "error", "message": f"integrity check failed: {integrity}"})
        return 1

    _print({"status": "ok", "path": str(target_dir), **manifest})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="YouTube Music Tuner operations")
    sub = parser.add_subparsers(dest="command", required=True)

    credentials = sub.add_parser("credentials", help="manage Google OAuth client credentials")
    credentials_sub = credentials.add_subparsers(dest="credentials_command", required=True)
    import_cmd = credentials_sub.add_parser("import", help="import client JSON ('-' for stdin)")
    import_cmd.add_argument("source", help="path to client_secret.json, or '-' for stdin")
    import_cmd.set_defaults(handler=cmd_credentials_import)

    auth = sub.add_parser("auth", help="run the OAuth device flow")
    auth.set_defaults(handler=cmd_auth)

    status = sub.add_parser("status", help="show local connection status")
    status.set_defaults(handler=cmd_status)

    backup = sub.add_parser("backup", help="create a verified database backup")
    backup.set_defaults(handler=cmd_backup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args, get_settings()))


if __name__ == "__main__":
    sys.exit(main())
