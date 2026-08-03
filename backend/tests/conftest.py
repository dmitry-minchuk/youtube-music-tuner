from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _reset_caches() -> None:
    from app.persistence import database
    from app.settings import get_settings

    get_settings.cache_clear()
    database.get_engine.cache_clear()
    database.get_session_factory.cache_clear()


def run_migrations(data_dir: Path) -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    directory = tmp_path / "data"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TUNER_DATA_DIR", str(directory))
    monkeypatch.setenv("TUNER_PUBLIC_PORT", "43127")
    _reset_caches()
    yield directory
    _reset_caches()


@pytest.fixture
def migrated_data_dir(data_dir: Path) -> Path:
    previous = os.environ.get("TUNER_DATA_DIR")
    os.environ["TUNER_DATA_DIR"] = str(data_dir)
    try:
        run_migrations(data_dir)
    finally:
        if previous is not None:
            os.environ["TUNER_DATA_DIR"] = previous
    return data_dir


@pytest.fixture
def fake_catalog():
    from tests.fakes import FakeCatalog

    return FakeCatalog()


@pytest.fixture
def app(migrated_data_dir: Path, fake_catalog):
    from app.main import create_app
    from app.settings import get_settings

    # The scheduler is driven explicitly in tests, never by a background loop.
    application = create_app(get_settings(), scheduler_enabled=False)
    application.state.catalog_factory = lambda _recorder: fake_catalog
    return application


@pytest.fixture
def db_session(migrated_data_dir: Path):
    from app.persistence.database import session_scope

    with session_scope() as session:
        yield session


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app, base_url="http://127.0.0.1:43127") as test_client:
        yield test_client


@pytest.fixture
def authed_client(client: TestClient) -> TestClient:
    """Client with a valid session cookie and CSRF header for mutations."""
    response = client.get("/api/v1/session")
    assert response.status_code == 200
    payload = response.json()
    client.headers.update({payload["headerName"]: payload["csrfToken"]})
    return client


@pytest.fixture(autouse=True)
def _no_verify_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verification retries wait for YouTube to catch up; tests must not."""
    from app.publishing import service

    monkeypatch.setattr(service, "VERIFY_BACKOFF_SECONDS", (0.0, 0.0, 0.0))
