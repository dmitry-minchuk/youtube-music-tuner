# Data model

## 1. Storage

SQLite in WAL mode is the primary persistent store. All tables use UTC ISO-8601 timestamps or integer epoch with a uniform mapping. External identifiers are stored as opaque strings.

UUID v4 is used for the local `session_id`, `event_id`, `generation_id`, `job_id`, `instance_id`. The database schema is managed by Alembic.

## 2. Core entities

### `tracks`

| Field | Purpose |
| --- | --- |
| `video_id` PK | YouTube video ID |
| `title` | normalised title |
| `metadata_duration_seconds` nullable | duration from library/catalogue metadata; player duration is stored separately in the session |
| `album_id`, `album_title` nullable | album |
| `thumbnail_url` nullable | external cover URL |
| `is_playable` | whether it can be queued |
| `metadata_json` | bounded forward-compatible remainder |
| `first_seen_at`, `updated_at`, `remote_deleted_at` | lifecycle |

Artists are normalised into `artists` and `track_artists(track_id, artist_id, ordinal)`. A missing album is not an error.

### `library_track_state`

One row per track:

- `is_liked`, `is_disliked`, `is_in_library`;
- `remote_rating_synced_at`;
- `desired_rating`, `rating_revision`, `rating_synced_revision`, `rating_sync_status`;
- `source_snapshot_id`, `updated_at`.

### `taste_vetoes`

The local strong negative "Don't Like At All" (docs/05 §11). A separate table, because affinity aggregates are idempotently rebuilt from sessions and cannot carry a manual signal:

- `video_id` — PK, FK to `tracks`;
- `artist_id` — the primary artist at the time of the veto (snapshot fallback; in a wave the artist is resolved anew from `track_artists`);
- `source` — `MANUAL` (button), `FARM_AUTO` (farm detector), `OVERRIDDEN` (an auto-veto lifted by the user; stored inert so that the detector does not overrule the human);
- `created_at`.

Never synced to YouTube.

### `remote_playlists` and `remote_playlist_items`

Snapshots of remote playlists, including order. For each snapshot `fetched_at`, `content_hash` and `track_count` are recorded.

### `managed_playlists`

- `managed_playlist_id` UUID PK;
- `playlist_id` nullable UNIQUE — the remote ID appears only after the create response;
- `kind`: FAMILIAR | BALANCE | DISCOVERY;
- `instance_id`;
- `ownership_marker`;
- `temperature`;
- `configured_target_size` default 60;
- `status`: CREATING | UNVERIFIED | ACTIVE | CLEANUP_REQUIRED | DELETED;
- `accepted_desired_hash` and setup timestamps/error code for crash-safe reconciliation;
- `last_published_at`, `next_publish_after`;
- `auto_publish_enabled`.

The setup intent is created before the external create. The playlist ID is saved and the status changes to UNVERIFIED immediately after the ID is returned, before the verification read. A regular write is allowed only for ACTIVE when the playlist ID, local instance and remote marker match. For UNVERIFIED/CLEANUP_REQUIRED only verify/adopt or an explicitly confirmed cleanup of the exact playlist with a matching marker is allowed; on restart these manifests are reconciled first and no duplicate is created. The transition `UNVERIFIED|CLEANUP_REQUIRED → DELETED` happens only after a successful `delete_playlist` or a fresh reconciliation confirming remote not-found; the tombstone keeps the instance/playlist ID, the marker, the deletion timestamp and the outcome ledger ID.

## 3. Telemetry

### `telemetry_events`

Append-only table:

- `client_event_id` UNIQUE;
- `session_id`, `sequence_no` with a UNIQUE pair;
- `event_type`, `video_id`;
- `occurred_at_client`, `received_at_server`;
- `monotonic_ms`;
- `schema_version`;
- `payload_json` after server-side validation.

Indexes: `(session_id, sequence_no)`, `(video_id, received_at_server)`, `(event_type, received_at_server)`.

### `playback_sessions`

- `session_id` PK, `video_id`, `queue_id`, `generation_id`;
- `started_at`, `ended_at`, `termination_reason`;
- `effective_duration_seconds` nullable, `duration_source`: PLAYER | METADATA | UNKNOWN;
- `played_seconds`, `played_ratio` nullable, `max_position_seconds`;
- `seek_forward_seconds`, `seek_backward_seconds`, `seek_forward_count`, `seek_backward_count`, `buffered_seconds`;
- `explicit_rating`, `early_skip`, `mid_skip`, `completed`, `ended_unqualified`, `large_forward_seek`, `replayed`;
- `classification_basis`: RATIO | ABSOLUTE_TIME;
- `qualified`, `reward`, `reward_version`;
- `source`: TUNER | REMOTE_HISTORY;
- `aggregation_version`, `aggregated_at`.

Raw events remain a source for recomputation only while they are within retention. After they are deleted, the session summary with the original `reward_version` and `aggregation_version` is an immutable historical record.

### `remote_history_items`

Stores only the available history fact and the timestamp/order. A row does not imitate a playback session and never has `played_seconds` or `skip`.

## 4. Candidate graph and features

### `candidate_edges`

- `seed_video_id`, `candidate_video_id`;
- `source_type`: RELATED | RADIO | MOOD | PLAYLIST;
- `source_key`, `rank`, `fetched_at`, `expires_at`;
- `hop` — distance from a positive root, 1..3;
- UNIQUE `(seed_video_id, candidate_video_id, source_type, source_key)`;
- indexes on `seed_video_id` and `candidate_video_id` for graph traversal.

`expires_at` is not the lifetime of the row but a mark that "the seed may be queried again". The edge stays in the graph after that date: it records a fact about the catalogue rather than caching a response. Ranking reads the whole graph, not a fresh window.

### `remote_playlists`

`track_count` is nullable. YouTube does not return a size for its system playlists (`LM` — Liked Music, `SE` — Episodes for Later), and writing zero passed off "unknown" as "empty". For Liked Music the API returns the locally known number of likes: it is the same entity, and we know that number exactly.

### `track_affinity` and `artist_affinity`

Materialised aggregates per track/artist:

- play counts 1/7/30/all time;
- completion/skip/replay counts;
- decayed reward — an exponentially weighted average with a 30-day half-life;
- last played/liked/skipped;
- confidence and aggregate version.

The table speeds up ranking but can be fully rebuilt from sessions/library.

The update happens **right after a session is re-aggregated**, not as a daily batch: a skip must affect the next track, not tomorrow's selection. The recomputation is always full per track (not incremental), because a telemetry batch may arrive again — this way the counters are not inflated. The `affinity_rollup` job recomputes everything every 6 hours: the sliding 1/7/30-day windows shrink over time even without new listens.

While these tables were empty, `fatigue`, `recent_skip`, `novelty` and `rediscovery` were identically zero, which means the listening history did not influence ranking at all.

### `feature_snapshots`

Stores the feature vector of every candidate that was actually selected, the serving policy/model ID and the feature schema at the moment of selection. This is the mandatory source for a correct online update: the model is trained on the features as of the moment of selection, not on recomputed future data.

## 5. Model and generations

### `model_snapshots`

- `model_id` UUID PK;
- `algorithm` = `linucb-v1`;
- `feature_schema_version`;
- `parameters_blob` — a compact NumPy-compatible binary or JSON for a small matrix;
- `trained_through_session_id/time`;
- `training_counts_json`, `metrics_json`;
- `status`: CANDIDATE | SHADOW | ACTIVE | REJECTED | RETIRED;
- `created_at`, `activated_at`.

Before activation an ACTIVE snapshot may be absent; after activation at most one snapshot per algorithm/schema is active at a time. SHADOW snapshots may exist in parallel and are not used by the serving ranker.

### `queue_generations` and `queue_items`

A generation stores the temperature, mood, serving policy, nullable serving/shadow model IDs, `quality_score_source`, configured/effective target size, random seed, candidate pool watermark and the full ordered result with score/reason codes. This provides reproducibility and explanations both for the rule baseline and for the ACTIVE LinUCB.

Two freshness metrics are additionally recorded: `pool_size` — how many playable candidates were available, and `overlap_previous_percent` — what share of this wave the previous one already contained. The latest generations are themselves an input to the next one (docs/05 §11), so reproducibility by `random_seed` means "the same database state plus the same seed", not "calling again back to back".

### `playlist_publications`

States:

```text
PLANNED → WRITING → VERIFYING → COMPLETE
                    │
                    ├────────→ PARTIAL → PLANNED
                    └────────→ FAILED
```

The record contains the remote-before snapshot, the immutable desired snapshot/hash, the target generation, `quality_gate_version` and the gate results, configured/effective target size, a bounded diff, remaining item/request counts, item-change/request budgets, the expected intermediate hash, applied operations, error code and verification hash. PARTIAL continues the same desired hash until it reaches COMPLETE or is explicitly invalidated by a safety event. The continuation does not wait for 15 new sessions, but has its own `not_before` of the next 24-hour window and again passes the ownership/hash/safety/budget checks.

## 6. Jobs and external calls

### `jobs`

- `job_id`, `job_type`, `dedupe_key` UNIQUE for an active job;
- `status`: PENDING | RUNNING | SUCCEEDED | FAILED | CANCELLED;
- `not_before`, `attempt`, `max_attempts`;
- `locked_by`, `locked_until`;
- `payload_json`, `result_json`, `last_error_code`;
- timestamps.

For rating, `dedupe_key=rating:{video_id}` is used, without the desired state in the key. The payload contains the latest desired state and revision; completing the job confirms only the revision it actually sent.

### `api_call_ledger`

- provider/operation;
- playlist/publication ID nullable and `request_kind`: READ | MUTATION;
- request fingerprint without secrets;
- job/request ID;
- started/finished/duration;
- outcome/status class;
- retry-after/circuit state;
- response size, but not the full response.

The ledger is used for the budget and diagnostics, not as an analytics export.

## 7. Settings and secrets

`settings` contains only non-secret values: default temperature, mood, automation flags, retention days, publish window. Secret files are kept separately in `/data/secrets` and are never written to SQLite.

`schema_metadata` stores the DB version, the app instance UUID and the creation time. The account ID, if needed to protect against accidentally connecting a different account, is stored in a hashed/minimal form and is shown by the UI from a fresh account DTO.

## 8. Retention

By default:

- raw telemetry events — 180 days;
- playback session summaries — indefinitely, until manually deleted;
- api call ledger — 90 days;
- failed job detail — 30 days; successful jobs — 7 days;
- candidate edges — deleted if not refreshed for a year; `expires_at` does not delete them (§4);
- feature snapshots — 180 days, in step with raw telemetry, after which the online update relies on the retained session/model aggregates;
- playlist backups — the last 30 snapshots per managed playlist;
- model snapshots — the active one + the last 10;
- daily database backups — 14 days.

Retention cleanup deletes only after a successful backup and never deletes session aggregates together with raw events.

A consequence of retention: changing the reward/aggregation formula recomputes the retained 180-day window and new events. Older summaries do not promise recomputation and are not rewritten; the baseline snapshot also stays frozen at the original formula version. The user may increase retention before data collection starts if they want a longer horizon of reproducible backfill.

## 9. Data deletion

`Delete telemetry` is executed transactionally:

1. create an optional user-requested backup only on explicit choice;
2. delete raw events, playback sessions, affinity, feature/model/queue snapshots;
3. keep the library cache and the managed playlist manifest unless the user chose a full reset;
4. record a local audit fact of the deletion without any music data;
5. recreate an empty model.

`Full reset` also deletes the OAuth files and the DB after the process is stopped. The operation must be described with exact paths and must require confirmation.
