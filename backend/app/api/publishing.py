"""Managed publishing endpoints (docs/08 section 7)."""

from __future__ import annotations

import random
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import errors
from app.api.deps import catalog_dep
from app.api.library import map_integration_error
from app.integrations.youtube_music.errors import IntegrationError
from app.integrations.youtube_music.ledger import BudgetExceeded, CallBudget
from app.integrations.youtube_music.port import MusicCatalogPort
from app.persistence.database import get_session
from app.persistence.models import (
    ManagedPlaylist,
    PlaylistBackup,
    PlaylistPublication,
    SchemaMetadata,
    utcnow,
)
from app.publishing.desired_list import DesiredListResult, build_desired_list
from app.publishing.quality_gates import MIN_PUBLISH_SIZE, QUALITY_GATE_VERSION
from app.publishing.service import (
    PLAYLIST_KINDS,
    PUBLISH_WINDOW,
    OwnershipError,
    PlaylistNotVerified,
    cleanup_setup_artifact,
    complete_setup,
    create_setup_intent,
    desired_hash,
    publish_window,
    reconcile_setup,
)

router = APIRouter(prefix="/api/v1/managed-playlists", tags=["publishing"])

Kind = Literal["FAMILIAR", "BALANCE", "DISCOVERY"]


def instance_id(db: Session) -> str:
    row = db.get(SchemaMetadata, "instance_id")
    if row is None:
        import uuid

        row = SchemaMetadata(key="instance_id", value=str(uuid.uuid4()))
        db.add(row)
        db.flush()
    return row.value


def _manifest(db: Session, kind: str) -> ManagedPlaylist:
    manifest = db.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == kind))
    if manifest is None:
        raise errors.ValidationFailed("managed playlist has not been set up yet")
    return manifest


def _serialize_track(track: Any) -> dict[str, Any]:
    return {
        "videoId": track.video_id,
        "title": track.title,
        "artists": list(track.artists),
        "familiarity": track.familiarity,
        "reasonCodes": list(track.reason_codes),
    }


def _serialize_plan(result: DesiredListResult) -> dict[str, Any]:
    return {
        "configuredTargetSize": result.configured_target_size,
        "effectiveTargetSize": result.effective_target_size,
        "minimumPublishSize": result.minimum_publish_size,
        "tracks": [_serialize_track(track) for track in result.tracks],
        "randomSeed": result.random_seed,
        "requiredFamiliar": result.required_familiar,
        "availableFamiliar": result.available_familiar,
        "requiredDiscovery": result.required_discovery,
        "availableDiscovery": result.available_discovery,
        "gateVersion": result.gate_version,
        "gateFailures": list(result.failures),
        "gateSkipped": list(result.skipped),
        "reasonCodes": list(result.reasons),
        "videoIds": list(result.video_ids),
    }


def _guard_budget(db: Session) -> None:
    budget = CallBudget(db)
    circuit = budget.circuit_state()
    if circuit.open:
        raise errors.CircuitOpen(f"external calls are paused ({circuit.reason})")


class PlanRequest(BaseModel):
    regenerate: bool = False


def _stored_proposal(db: Session, kind: str) -> dict[str, Any] | None:
    manifest = db.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == kind))
    if manifest is None:
        return None
    stored = manifest.proposed_desired_json
    return stored if isinstance(stored, dict) and stored.get("tracks") else None


def _remember_proposal(db: Session, kind: str, result: DesiredListResult) -> None:
    manifest = db.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == kind))
    if manifest is None or not result.passed:
        return
    manifest.proposed_desired_json = {
        "randomSeed": result.random_seed,
        "generatedAt": utcnow().isoformat() + "Z",
        "configuredTargetSize": result.configured_target_size,
        "effectiveTargetSize": result.effective_target_size,
        "tracks": [_serialize_track(track) for track in result.tracks],
    }
    manifest.accepted_desired_hash = desired_hash(list(result.video_ids))
    db.flush()


@router.post("/{kind}/plan")
def plan_playlist(
    kind: Kind,
    body: PlanRequest | None = None,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Local preview — never calls YouTube.

    The reviewed list is remembered so Publish writes exactly what was shown.
    Asking again returns that same list; ``regenerate`` replaces it with a
    fresh candidate.
    """
    regenerate = bool(body.regenerate) if body is not None else False
    stored = None if regenerate else _stored_proposal(db, kind)
    if stored is not None:
        return {
            "status": "READY",
            "configuredTargetSize": stored.get("configuredTargetSize", 0),
            "effectiveTargetSize": stored.get("effectiveTargetSize", 0),
            "minimumPublishSize": MIN_PUBLISH_SIZE,
            "tracks": stored.get("tracks", []),
            "randomSeed": stored.get("randomSeed"),
            "generatedAt": stored.get("generatedAt"),
            "requiredFamiliar": 0,
            "availableFamiliar": 0,
            "requiredDiscovery": 0,
            "availableDiscovery": 0,
            "gateVersion": QUALITY_GATE_VERSION,
            "gateFailures": [],
            "gateSkipped": [],
            "reasonCodes": [],
            "videoIds": [track["videoId"] for track in stored.get("tracks", [])],
        }

    seed = random.randrange(2**31) if regenerate else None
    result = build_desired_list(db, kind, random_seed=seed)
    _remember_proposal(db, kind, result)
    body_out = _serialize_plan(result)
    body_out["status"] = "READY" if result.passed else "SKIPPED_QUALITY"
    if not result.passed:
        body_out["reasonCode"] = (
            "INSUFFICIENT_POOL" if result.effective_target_size == 0 else "QUALITY_GATE_FAILED"
        )
    return body_out


class SetupRequest(BaseModel):
    acceptedDesiredHash: str | None = None


@router.post("/setup")
def setup_playlists(
    body: SetupRequest,
    db: Session = Depends(get_session),
    catalog: MusicCatalogPort = Depends(catalog_dep),
) -> dict[str, Any]:
    """Create or reconcile the three Tuner playlists after an explicit preview."""
    _guard_budget(db)
    instance = instance_id(db)
    results: list[dict[str, Any]] = []

    for kind in PLAYLIST_KINDS:
        existing = db.scalar(select(ManagedPlaylist).where(ManagedPlaylist.kind == kind))
        if existing is not None and existing.status == "ACTIVE":
            # Setup is idempotent: an ACTIVE playlist needs nothing, and
            # falling through to a create would mint a duplicate on YouTube.
            results.append(
                {
                    "kind": kind,
                    "managedPlaylistId": existing.managed_playlist_id,
                    "playlistId": existing.playlist_id,
                    "status": "ACTIVE",
                    "alreadyExisting": True,
                    "errorCode": None,
                }
            )
            continue
        # A create that failed with a stored error may still have gone
        # through remotely — adopt by marker before ever creating again
        # (docs/03 s.8). Captured here because the intent reset clears it.
        adopt_first = (
            existing is not None
            and existing.status == "CREATING"
            and existing.playlist_id is None
            and existing.setup_error_code is not None
        )

        desired_result = build_desired_list(db, kind)
        if not desired_result.passed:
            results.append(
                {
                    "kind": kind,
                    "status": "SKIPPED_QUALITY",
                    "reasonCode": (
                        "INSUFFICIENT_POOL"
                        if desired_result.effective_target_size == 0
                        else "QUALITY_GATE_FAILED"
                    ),
                    **_serialize_plan(desired_result),
                }
            )
            continue

        desired = list(desired_result.video_ids)
        manifest = create_setup_intent(
            db,
            kind,
            instance,
            desired,
            configured_target_size=desired_result.configured_target_size,
        )
        try:
            if manifest.status in {"UNVERIFIED", "CLEANUP_REQUIRED"} or (
                manifest.status == "CREATING" and manifest.playlist_id is not None
            ):
                outcome = reconcile_setup(db, manifest, catalog, desired)
            elif adopt_first:
                outcome = reconcile_setup(db, manifest, catalog, desired)
                if outcome.status == "CREATING" and outcome.error_code is None:
                    outcome = complete_setup(db, manifest, catalog, desired)
            else:
                outcome = complete_setup(db, manifest, catalog, desired)
        except IntegrationError as exc:
            raise map_integration_error(exc) from exc

        results.append(
            {
                "kind": kind,
                "managedPlaylistId": outcome.managed_playlist_id,
                "playlistId": outcome.playlist_id,
                "status": outcome.status,
                "errorCode": outcome.error_code,
                **_serialize_plan(desired_result),
            }
        )

    return {"playlists": results}


@router.post("/{kind}/reconcile")
def reconcile(
    kind: Kind,
    db: Session = Depends(get_session),
    catalog: MusicCatalogPort = Depends(catalog_dep),
) -> dict[str, Any]:
    _guard_budget(db)
    manifest = _manifest(db, kind)
    if manifest.status == "DELETED":
        # Reconcile adopts and verifies; it never creates. Resurrecting a
        # tombstone into CREATING made a deleted playlist look forever "in
        # progress" (docs/03 s.8: DELETED is terminal for reconcile).
        raise errors.ValidationFailed(
            "the playlist was deleted — create it again from the Playlists page"
        )
    # Verification compares the remote list against the hash recorded when the
    # playlist was written, so there is nothing to regenerate here.
    try:
        outcome = reconcile_setup(db, manifest, catalog, [])
    except IntegrationError as exc:
        raise map_integration_error(exc) from exc
    return {
        "kind": kind,
        "status": outcome.status,
        "playlistId": outcome.playlist_id,
        "errorCode": outcome.error_code,
    }


@router.delete("/{kind}/setup-artifact")
def delete_setup_artifact(
    kind: Kind,
    db: Session = Depends(get_session),
    catalog: MusicCatalogPort = Depends(catalog_dep),
) -> dict[str, Any]:
    """Delete only our own unverified artifact, after an explicit confirmation."""
    manifest = _manifest(db, kind)
    try:
        status = cleanup_setup_artifact(db, manifest, catalog)
    except OwnershipError as exc:
        raise errors.PlaylistNotManaged(str(exc)) from exc
    except IntegrationError as exc:
        raise map_integration_error(exc) from exc
    return {"kind": kind, "status": status}


@router.post("/{kind}/publish")
def publish(
    kind: Kind,
    db: Session = Depends(get_session),
    catalog: MusicCatalogPort = Depends(catalog_dep),
) -> dict[str, Any]:
    _guard_budget(db)
    manifest = _manifest(db, kind)
    if manifest.playlist_id is None:
        raise errors.PlaylistUnverified("playlist has no verified remote id yet")

    now = utcnow()
    if manifest.next_publish_after is not None and manifest.next_publish_after > now:
        raise errors.LocalBudgetExceeded(
            "the daily publish window has not opened yet",
            nextAllowedAt=manifest.next_publish_after.isoformat() + "Z",
        )

    # The window starts at the last publish, or 24 hours back for the first one.
    window_start = manifest.last_published_at or (now - PUBLISH_WINDOW)
    try:
        CallBudget(db, now).check_publish_window(manifest.playlist_id, window_start)
    except BudgetExceeded as exc:
        raise errors.LocalBudgetExceeded(str(exc), budget=exc.budget, limit=exc.limit) from exc

    # Publish what was reviewed. Regenerating here would write a list the user
    # never saw, which is the whole point of the preview step.
    stored = _stored_proposal(db, kind)
    if stored is not None:
        desired = [track["videoId"] for track in stored.get("tracks", [])]
    else:
        desired_result = build_desired_list(db, kind)
        if not desired_result.passed:
            raise errors.PlaylistQualityFailed(
                "the desired playlist did not pass the quality gates",
                gateFailures=list(desired_result.failures),
                **_serialize_plan(desired_result),
            )
        desired = list(desired_result.video_ids)
        _remember_proposal(db, kind, desired_result)

    try:
        publication = publish_window(db, manifest, catalog, desired)
    except PlaylistNotVerified as exc:
        raise errors.PlaylistUnverified(str(exc)) from exc
    except OwnershipError as exc:
        raise errors.PlaylistNotManaged(str(exc)) from exc
    except IntegrationError as exc:
        raise map_integration_error(exc) from exc

    return _serialize_publication(publication)


def _serialize_publication(publication: PlaylistPublication) -> dict[str, Any]:
    return {
        "publicationId": publication.publication_id,
        "status": publication.status,
        "desiredHash": publication.desired_hash,
        "effectiveTargetSize": publication.effective_target_size,
        "remainingItemChanges": publication.remaining_item_changes,
        "remainingEstimatedRequests": publication.remaining_estimated_requests,
        "nextContinuationAfter": (
            publication.not_before.isoformat() + "Z" if publication.not_before else None
        ),
        "errorCode": publication.error_code,
        "verificationHash": publication.verification_hash,
        "gateVersion": publication.quality_gate_version,
    }


@router.get("/{kind}/backups")
def list_backups(kind: Kind, db: Session = Depends(get_session)) -> dict[str, Any]:
    manifest = _manifest(db, kind)
    rows = db.scalars(
        select(PlaylistBackup)
        .where(PlaylistBackup.managed_playlist_id == manifest.managed_playlist_id)
        .order_by(PlaylistBackup.created_at.desc())
    ).all()
    return {
        "kind": kind,
        "backups": [
            {
                "backupId": row.backup_id,
                "createdAt": row.created_at.isoformat() + "Z",
                "trackCount": len(row.items_json.get("videoIds", [])),
                "contentHash": row.content_hash,
            }
            for row in rows
        ],
    }


class RestoreRequest(BaseModel):
    backupId: str


@router.post("/{kind}/restore")
def plan_restore(
    kind: Kind, body: RestoreRequest, db: Session = Depends(get_session)
) -> dict[str, Any]:
    """Plan a manual restore; the write itself still goes through publish."""
    manifest = _manifest(db, kind)
    backup = db.get(PlaylistBackup, body.backupId)
    if backup is None or backup.managed_playlist_id != manifest.managed_playlist_id:
        raise errors.ValidationFailed("unknown backup for this playlist")
    return {
        "kind": kind,
        "backupId": backup.backup_id,
        "videoIds": backup.items_json.get("videoIds", []),
        "note": "Review the list, then publish it as the desired order.",
    }
