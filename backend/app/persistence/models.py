"""SQLAlchemy models — the complete schema from docs/07-data-model.md.

All timestamps are stored as naive UTC datetimes. External identifiers are
opaque strings; local identifiers are UUID v4 strings.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[str]: JSON}


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


class Track(Base):
    __tablename__ = "tracks"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    metadata_duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    album_id: Mapped[str | None] = mapped_column(String(128), default=None)
    album_title: Mapped[str | None] = mapped_column(Text, default=None)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, default=None)
    is_playable: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    remote_deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)


class Artist(Base):
    __tablename__ = "artists"

    artist_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class TrackArtist(Base):
    __tablename__ = "track_artists"

    track_id: Mapped[str] = mapped_column(ForeignKey("tracks.video_id"), primary_key=True)
    artist_id: Mapped[str] = mapped_column(ForeignKey("artists.artist_id"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)


class LibraryTrackState(Base):
    __tablename__ = "library_track_state"

    video_id: Mapped[str] = mapped_column(ForeignKey("tracks.video_id"), primary_key=True)
    is_liked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_disliked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_in_library: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_rating_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    desired_rating: Mapped[str | None] = mapped_column(String(16), default=None)
    rating_revision: Mapped[int] = mapped_column(Integer, default=0)
    rating_synced_revision: Mapped[int] = mapped_column(Integer, default=0)
    rating_sync_status: Mapped[str] = mapped_column(String(24), default="SYNCED")
    source_snapshot_id: Mapped[str | None] = mapped_column(String(36), default=None)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RemotePlaylist(Base):
    __tablename__ = "remote_playlists"

    playlist_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    track_count: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    remote_deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)


class RemotePlaylistItem(Base):
    __tablename__ = "remote_playlist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    playlist_id: Mapped[str] = mapped_column(ForeignKey("remote_playlists.playlist_id"))
    position: Mapped[int] = mapped_column(Integer)
    video_id: Mapped[str] = mapped_column(String(64))
    set_video_id: Mapped[str | None] = mapped_column(String(128), default=None)

    __table_args__ = (UniqueConstraint("playlist_id", "position", name="uq_remote_item_position"),)


class ManagedPlaylist(Base):
    """docs/07 section 2 — nullable remote ID with crash-safe setup lifecycle."""

    __tablename__ = "managed_playlists"

    managed_playlist_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    playlist_id: Mapped[str | None] = mapped_column(String(128), unique=True, default=None)
    kind: Mapped[str] = mapped_column(String(16))
    instance_id: Mapped[str] = mapped_column(String(36))
    ownership_marker: Mapped[str] = mapped_column(Text)
    temperature: Mapped[int] = mapped_column(Integer)
    configured_target_size: Mapped[int] = mapped_column(Integer, default=60)
    status: Mapped[str] = mapped_column(String(24), default="CREATING")
    accepted_desired_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    setup_started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    setup_finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    setup_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    last_published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    next_publish_after: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    auto_publish_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("kind", name="uq_managed_playlist_kind"),)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_event_id: Mapped[str] = mapped_column(String(36), unique=True)
    session_id: Mapped[str] = mapped_column(String(36))
    sequence_no: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    video_id: Mapped[str] = mapped_column(String(64))
    occurred_at_client: Mapped[dt.datetime] = mapped_column(DateTime)
    received_at_server: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    monotonic_ms: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    __table_args__ = (
        UniqueConstraint("session_id", "sequence_no", name="uq_event_session_sequence"),
        Index("ix_events_session_sequence", "session_id", "sequence_no"),
        Index("ix_events_video_received", "video_id", "received_at_server"),
        Index("ix_events_type_received", "event_type", "received_at_server"),
    )


class PlaybackSession(Base):
    __tablename__ = "playback_sessions"

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    video_id: Mapped[str] = mapped_column(String(64))
    queue_id: Mapped[str | None] = mapped_column(String(36), default=None)
    generation_id: Mapped[str | None] = mapped_column(String(36), default=None)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    termination_reason: Mapped[str | None] = mapped_column(String(32), default=None)
    effective_duration_seconds: Mapped[float | None] = mapped_column(Float, default=None)
    duration_source: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    played_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    played_ratio: Mapped[float | None] = mapped_column(Float, default=None)
    max_position_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    seek_forward_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    seek_backward_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    seek_forward_count: Mapped[int] = mapped_column(Integer, default=0)
    seek_backward_count: Mapped[int] = mapped_column(Integer, default=0)
    buffered_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    wall_clock_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    explicit_rating: Mapped[str | None] = mapped_column(String(16), default=None)
    early_skip: Mapped[bool] = mapped_column(Boolean, default=False)
    mid_skip: Mapped[bool] = mapped_column(Boolean, default=False)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    ended_unqualified: Mapped[bool] = mapped_column(Boolean, default=False)
    large_forward_seek: Mapped[bool] = mapped_column(Boolean, default=False)
    replayed: Mapped[bool] = mapped_column(Boolean, default=False)
    classification_basis: Mapped[str] = mapped_column(String(16), default="RATIO")
    qualified: Mapped[bool] = mapped_column(Boolean, default=False)
    reward: Mapped[float | None] = mapped_column(Float, default=None)
    reward_version: Mapped[str | None] = mapped_column(String(24), default=None)
    source: Mapped[str] = mapped_column(String(16), default="TUNER")
    aggregation_version: Mapped[str | None] = mapped_column(String(24), default=None)
    aggregated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (Index("ix_sessions_started", "started_at"),)


class RemoteHistoryItem(Base):
    __tablename__ = "remote_history_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    play_order: Mapped[int] = mapped_column(Integer, default=0)
    period_label: Mapped[str | None] = mapped_column(String(64), default=None)

    __table_args__ = (
        UniqueConstraint("video_id", "observed_at", "play_order", name="uq_remote_history_item"),
    )


# --------------------------------------------------------------------------
# Candidate graph, features, model
# --------------------------------------------------------------------------


class CandidateEdge(Base):
    __tablename__ = "candidate_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seed_video_id: Mapped[str] = mapped_column(String(64))
    candidate_video_id: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(16))
    source_key: Mapped[str] = mapped_column(String(128), default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)

    __table_args__ = (
        UniqueConstraint(
            "seed_video_id",
            "candidate_video_id",
            "source_type",
            "source_key",
            name="uq_candidate_edge",
        ),
        Index("ix_candidate_expires", "expires_at"),
    )


class TrackAffinity(Base):
    __tablename__ = "track_affinity"

    video_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plays_1d: Mapped[int] = mapped_column(Integer, default=0)
    plays_7d: Mapped[int] = mapped_column(Integer, default=0)
    plays_30d: Mapped[int] = mapped_column(Integer, default=0)
    plays_all: Mapped[int] = mapped_column(Integer, default=0)
    completions: Mapped[int] = mapped_column(Integer, default=0)
    skips: Mapped[int] = mapped_column(Integer, default=0)
    replays: Mapped[int] = mapped_column(Integer, default=0)
    decayed_reward: Mapped[float] = mapped_column(Float, default=0.0)
    last_played_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    last_liked_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    last_skipped_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    aggregate_version: Mapped[str] = mapped_column(String(24), default="affinity-v1")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ArtistAffinity(Base):
    __tablename__ = "artist_affinity"

    artist_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plays_1d: Mapped[int] = mapped_column(Integer, default=0)
    plays_7d: Mapped[int] = mapped_column(Integer, default=0)
    plays_30d: Mapped[int] = mapped_column(Integer, default=0)
    plays_all: Mapped[int] = mapped_column(Integer, default=0)
    decayed_reward: Mapped[float] = mapped_column(Float, default=0.0)
    last_played_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FeatureSnapshot(Base):
    """Feature vector at selection time — required for correct online update."""

    __tablename__ = "feature_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(String(36), default=None)
    generation_id: Mapped[str] = mapped_column(String(36))
    video_id: Mapped[str] = mapped_column(String(64))
    feature_schema_version: Mapped[str] = mapped_column(String(24))
    features_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        UniqueConstraint("generation_id", "video_id", name="uq_feature_generation_video"),
        Index("ix_feature_session", "session_id"),
        Index("ix_feature_created", "created_at"),
    )


class ModelSnapshot(Base):
    __tablename__ = "model_snapshots"

    model_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    algorithm: Mapped[str] = mapped_column(String(32), default="linucb-v1")
    feature_schema_version: Mapped[str] = mapped_column(String(24))
    parameters_blob: Mapped[bytes] = mapped_column(LargeBinary)
    trained_through_session_id: Mapped[str | None] = mapped_column(String(36), default=None)
    trained_through_time: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    training_counts_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="CANDIDATE")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (Index("ix_model_status", "status"),)


class QueueGeneration(Base):
    __tablename__ = "queue_generations"

    generation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    queue_id: Mapped[str] = mapped_column(String(36))
    temperature: Mapped[int] = mapped_column(Integer)
    mood: Mapped[str] = mapped_column(String(16), default="ANY")
    serving_policy: Mapped[str] = mapped_column(String(32))
    serving_model_id: Mapped[str | None] = mapped_column(String(36), default=None)
    shadow_model_id: Mapped[str | None] = mapped_column(String(36), default=None)
    quality_score_source: Mapped[str] = mapped_column(String(24), default="RULE_MAPPED")
    random_seed: Mapped[int] = mapped_column(Integer)
    pool_watermark: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    target_familiar_percent: Mapped[int] = mapped_column(Integer, default=0)
    actual_familiar_percent: Mapped[int] = mapped_column(Integer, default=0)
    relaxations_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_generation_queue", "queue_id"),)


class QueueItem(Base):
    __tablename__ = "queue_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    generation_id: Mapped[str] = mapped_column(ForeignKey("queue_generations.generation_id"))
    queue_id: Mapped[str] = mapped_column(String(36))
    position: Mapped[int] = mapped_column(Integer)
    video_id: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    quality_expected: Mapped[float] = mapped_column(Float, default=0.0)
    familiarity: Mapped[str] = mapped_column(String(16), default="DISCOVERY")
    reason_codes_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    played_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        UniqueConstraint("generation_id", "position", name="uq_queue_item_position"),
        Index("ix_queue_items_queue", "queue_id", "position"),
    )


class PlaylistPublication(Base):
    __tablename__ = "playlist_publications"

    publication_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    managed_playlist_id: Mapped[str] = mapped_column(
        ForeignKey("managed_playlists.managed_playlist_id")
    )
    status: Mapped[str] = mapped_column(String(16), default="PLANNED")
    target_generation_id: Mapped[str | None] = mapped_column(String(36), default=None)
    quality_gate_version: Mapped[str] = mapped_column(String(24), default="playlist-gates-v2")
    quality_gate_results_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    configured_target_size: Mapped[int] = mapped_column(Integer, default=60)
    effective_target_size: Mapped[int] = mapped_column(Integer, default=60)
    remote_before_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    desired_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    desired_hash: Mapped[str] = mapped_column(String(64))
    expected_intermediate_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    applied_operations_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    remaining_item_changes: Mapped[int] = mapped_column(Integer, default=0)
    remaining_estimated_requests: Mapped[int] = mapped_column(Integer, default=0)
    verification_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    not_before: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    __table_args__ = (Index("ix_publication_playlist_status", "managed_playlist_id", "status"),)


class PlaylistBackup(Base):
    __tablename__ = "playlist_backups"

    backup_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    managed_playlist_id: Mapped[str] = mapped_column(
        ForeignKey("managed_playlists.managed_playlist_id")
    )
    publication_id: Mapped[str | None] = mapped_column(String(36), default=None)
    items_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (Index("ix_backup_playlist_created", "managed_playlist_id", "created_at"),)


# --------------------------------------------------------------------------
# Jobs, ledger, settings
# --------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32))
    dedupe_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    not_before: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    locked_by: Mapped[str | None] = mapped_column(String(64), default=None)
    locked_until: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_error_code: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        Index("ix_jobs_status_not_before", "status", "not_before"),
        # Only one active job per dedupe key (docs/07 section 6).
        Index(
            "uq_jobs_active_dedupe",
            "dedupe_key",
            unique=True,
            sqlite_where=text("status IN ('PENDING', 'RUNNING')"),
        ),
    )


class ApiCallLedger(Base):
    __tablename__ = "api_call_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32), default="ytmusic")
    operation: Mapped[str] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(128), default="")
    job_id: Mapped[str | None] = mapped_column(String(36), default=None)
    request_id: Mapped[str | None] = mapped_column(String(36), default=None)
    playlist_id: Mapped[str | None] = mapped_column(String(128), default=None)
    publication_id: Mapped[str | None] = mapped_column(String(36), default=None)
    is_mutation: Mapped[bool] = mapped_column(Boolean, default=False)
    counts_against_automatic_budget: Mapped[bool] = mapped_column(Boolean, default=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    outcome: Mapped[str] = mapped_column(String(24), default="SUCCESS")
    status_class: Mapped[str | None] = mapped_column(String(16), default=None)
    retry_after_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    circuit_state: Mapped[str | None] = mapped_column(String(16), default=None)
    response_size_bytes: Mapped[int | None] = mapped_column(Integer, default=None)

    __table_args__ = (
        Index("ix_ledger_started", "started_at"),
        Index("ix_ledger_operation_started", "operation", "started_at"),
    )


class SyncRun(Base):
    __tablename__ = "sync_runs"

    sync_run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING")
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, default=None)
    liked_count: Mapped[int] = mapped_column(Integer, default=0)
    playlist_count: Mapped[int] = mapped_column(Integer, default=0)
    history_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), default=None)


class AppSetting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SchemaMetadata(Base):
    __tablename__ = "schema_metadata"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
