"""Managed playlist endpoints: lifecycle honesty (docs/03 s.8, docs/08 s.7)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.persistence.models import ManagedPlaylist
from app.publishing.service import marker_for
from tests.fakes import SetupCatalog
from tests.test_desired_list_construction import build_library

INSTANCE = "inst-api"


def _manifest(db, kind: str, status: str, playlist_id: str | None = None) -> ManagedPlaylist:
    row = ManagedPlaylist(
        managed_playlist_id=f"mp-{kind}",
        kind=kind,
        instance_id=INSTANCE,
        ownership_marker=marker_for(INSTANCE),
        temperature=50,
        status=status,
        playlist_id=playlist_id,
    )
    db.add(row)
    return row


def test_reconcile_refuses_a_deleted_manifest(authed_client: TestClient, db_session) -> None:
    """Reconcile adopts and verifies; it must not resurrect a tombstone into
    an eternal CREATING zombie (the exact state this regression produced)."""
    _manifest(db_session, "FAMILIAR", "DELETED")
    db_session.commit()

    response = authed_client.post("/api/v1/managed-playlists/FAMILIAR/reconcile")
    assert response.status_code == 400

    db_session.expire_all()
    row = db_session.query(ManagedPlaylist).filter_by(kind="FAMILIAR").one()
    assert row.status == "DELETED"


def test_playlists_listing_hides_deleted_tombstones(authed_client: TestClient, db_session) -> None:
    _manifest(db_session, "FAMILIAR", "DELETED")
    _manifest(db_session, "BALANCE", "ACTIVE", playlist_id="PLb")
    db_session.commit()

    body = authed_client.get("/api/v1/playlists").json()
    kinds = {row["kind"] for row in body["tunerPlaylists"]}
    assert kinds == {"BALANCE"}
    assert body["tunerPlaylists"][0]["setupErrorCode"] is None


def test_setup_skips_active_playlists_instead_of_duplicating(
    app, authed_client: TestClient, db_session
) -> None:
    """An ACTIVE kind must never reach the create call: that minted a
    duplicate playlist on YouTube."""
    for kind, playlist_id in (("FAMILIAR", "PLf"), ("BALANCE", "PLb"), ("DISCOVERY", "PLd")):
        _manifest(db_session, kind, "ACTIVE", playlist_id=playlist_id)
    db_session.commit()

    catalog = SetupCatalog()
    app.state.catalog_factory = lambda _recorder: catalog

    response = authed_client.post("/api/v1/managed-playlists/setup", json={})
    assert response.status_code == 200
    results = response.json()["playlists"]
    assert all(item["status"] == "ACTIVE" for item in results)
    assert all(item.get("alreadyExisting") for item in results)
    assert catalog.created == []

    db_session.expire_all()
    statuses = {row.kind: row.status for row in db_session.query(ManagedPlaylist).all()}
    assert statuses == {"FAMILIAR": "ACTIVE", "BALANCE": "ACTIVE", "DISCOVERY": "ACTIVE"}


def test_setup_adopts_before_creating_after_a_failed_create(
    app, authed_client: TestClient, db_session
) -> None:
    """A create that errored out may still have gone through remotely: the
    next setup must adopt by marker, not mint a second playlist."""
    from app.domain.catalog import RemotePlaylist
    from app.persistence.models import SchemaMetadata

    build_library(db_session)
    db_session.add(SchemaMetadata(key="instance_id", value=INSTANCE))
    row = _manifest(db_session, "FAMILIAR", "CREATING")
    row.setup_error_code = "YTM_UNAVAILABLE"
    db_session.commit()

    remote = RemotePlaylist(
        playlist_id="PLorphan",
        title="Tuner · Familiar",
        description=marker_for(INSTANCE),
    )
    catalog = SetupCatalog(playlists=[remote])
    app.state.catalog_factory = lambda _recorder: catalog

    response = authed_client.post("/api/v1/managed-playlists/setup", json={})
    assert response.status_code == 200
    by_kind = {item["kind"]: item for item in response.json()["playlists"]}
    familiar = by_kind["FAMILIAR"]

    # Adopted the orphan: no second create for this kind.
    assert familiar["playlistId"] == "PLorphan"
    created_titles = [title for title, _, _ in catalog.created]
    assert "Tuner · Familiar" not in created_titles
