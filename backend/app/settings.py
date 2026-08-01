"""Application settings.

Only non-secret configuration lives here. OAuth client/token are read from
files under ``<data_dir>/secrets`` and are never passed via environment
variables (docs/09-operations-docker.md section 5).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TUNER_", extra="ignore")

    bind_host: str = "0.0.0.0"  # noqa: S104 - container-internal only, host mapping is loopback
    port: int = 43127
    public_port: int = 43127
    data_dir: Path = Path("/data")
    log_level: str = "INFO"
    auto_publish: bool = False
    raw_event_retention_days: int = 180

    @property
    def database_path(self) -> Path:
        return self.data_dir / "tuner.db"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.database_path}"

    @property
    def secrets_dir(self) -> Path:
        return self.data_dir / "secrets"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def oauth_file(self) -> Path:
        return self.secrets_dir / "oauth.json"

    @property
    def client_secret_file(self) -> Path:
        return self.secrets_dir / "client.json"

    @property
    def browser_auth_file(self) -> Path:
        """Browser request headers — the working auth path (docs/03 s.2)."""
        return self.secrets_dir / "browser.json"

    @property
    def allowed_hosts(self) -> frozenset[str]:
        """Host header allowlist, blocking DNS rebinding (docs/08 section 1)."""
        names = ("127.0.0.1", "localhost", "[::1]")
        return frozenset(f"{name}:{self.public_port}" for name in names) | frozenset(names)

    @property
    def allowed_origins(self) -> frozenset[str]:
        names = ("127.0.0.1", "localhost", "[::1]")
        return frozenset(f"http://{name}:{self.public_port}" for name in names)


@lru_cache
def get_settings() -> Settings:
    return Settings()
