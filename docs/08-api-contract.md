# Internal HTTP API

## 1. General rules

- Base path: `/api/v1`.
- JSON in `camelCase` externally, typed Python models internally.
- All timestamps are UTC RFC 3339.
- A mutation accepts `Idempotency-Key` when the operation may be repeated by the browser.
- An error has the form `{"error":{"code":"...","message":"...","requestId":"...","retryable":false}}`.
- The UI is same-origin; CORS is disabled by default.
- The backend rejects an unknown `Host` before routing. Allowlist: a hostname from `127.0.0.1`, `localhost`, `[::1]` and a port equal to `TUNER_PUBLIC_PORT`; this is a defence against DNS rebinding, not a feature of a future LAN mode.
- `GET /api/v1/session` sets a random HttpOnly `SameSite=Strict` session cookie and returns the associated CSRF token. Each mutation passes the token in `X-CSRF-Token`; the backend checks the cookie/token pair and the exact `Origin`. The session lives only locally and is invalidated after a restart.

## 2. System/Auth

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health/live` | liveness without calls to external services |
| GET | `/health/ready` | DB and migrations ready |
| GET | `/api/v1/session` | local session cookie + CSRF token |
| GET | `/api/v1/system/status` | versions, jobs, circuit, last sync/train/publish |
| GET | `/api/v1/auth/status` | connected account summary without secrets |
| POST | `/api/v1/auth/disconnect` | cancel jobs and delete local OAuth after confirmation |

OAuth bootstrap in the MVP is performed by the CLI, and the UI shows step-by-step instructions and automatically picks up the token file once it appears.

## 3. Library and playlists

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/library/tracks?view=liked&cursor=...` | local catalogue |
| GET | `/api/v1/playlists` | remote + managed summaries; managed ones carry `contentUpdatedAt` — the date of the latest content release (creation counts as the first) |
| GET | `/api/v1/playlists/{id}` | cached snapshot |
| POST | `/api/v1/sync` | enqueue a sync job with a cooldown |
| GET | `/api/v1/search?q=...` | cached/explicit remote search |
| PUT | `/api/v1/tracks/{videoId}/rating` | desired LIKE/DISLIKE/INDIFFERENT |
| POST | `/api/v1/tracks/{videoId}/veto` | local strong negative (docs/05 §11) |
| DELETE | `/api/v1/tracks/{videoId}/veto` | remove the veto |

Pagination cursor opaque; default page 50, maximum 200.

The rating response returns `desiredState`, `revision`, `syncStatus` and `vetoed` (in both responses — GET and PUT). Repeated PUTs for the same `videoId` update one logical command; the job dedupe key does not include the state. Veto endpoints do not create a sync job: the signal is strictly local. The `veto_set` telemetry event is in the allowlist and is aggregated as an explicit DISLIKE.

## 4. Wave and queue

`POST /api/v1/waves` creates a queue snapshot.

Request:

```json
{
  "temperature": 50,
  "mood": "FOCUS",
  "length": 40,
  "excludeVideoIds": ["already-played-id"]
}
```

Response:

```json
{
  "queueId": "uuid",
  "generationId": "uuid",
  "ranking": {
    "phase": "BASELINE",
    "servingPolicy": "rule-score-v1",
    "servingModelId": null,
    "shadowModelId": null,
    "qualityScoreSource": "RULE_MAPPED"
  },
  "mix": {"targetFamiliarPercent": 55, "actualFamiliarPercent": 50, "actualDiscoveryPercent": 50},
  "freshness": {"poolSize": 741, "overlapPreviousPercent": 0},
  "relaxations": ["FAMILIAR_POOL_WIDENED"],
  "items": [
    {
      "position": 1,
      "track": {"videoId": "...", "title": "...", "artists": ["..."]},
      "reasonCodes": ["RELATED_TO_POSITIVE_SEED", "ARTIST_NOT_RECENT"],
      "familiarity": "DISCOVERY"
    }
  ]
}
```

`freshness` reflects the target metric from docs/01 BR-012: how many playable candidates were available and what share of this wave the previous one contained. Both values are stored on the generation, so the trend is visible in Insights.

`relaxations` lists the declared deviations from the target composition; possible codes: `FAMILIAR_POOL_WIDENED`, `DISCOVERY_POOL_WIDENED`, `FAMILIAR_ROTATION_CAP`, `CONTEXT_WIDENED`, `DISCOVERY_REPEAT_ALLOWED`, `FAMILIAR_REPEAT_ALLOWED`, `SOURCE_CONCENTRATION_RELAXED`, `ARTIST_WINDOW_15_RELAXED`, `ARTIST_WINDOW_5_RELAXED`, `LOW_SEED_DIVERSITY` (the last one is at the level of item reason codes). A deviation without a code is a defect.

Reproducibility by `randomSeed` means "the same database state plus the same seed". The latest generations are themselves an input to the next one, so two consecutive calls intentionally produce different queues — this is a requirement, not nondeterminism.

After bootstrap and before activation, the SHADOW response explicitly shows the model that is being trained but is not the serving model:

```json
{
  "ranking": {
    "phase": "SHADOW",
    "servingPolicy": "rule-score-v1",
    "servingModelId": null,
    "shadowModelId": "uuid",
    "qualityScoreSource": "RULE_MAPPED"
  }
}
```

In the BASELINE and SHADOW phases, `rule-score-v1` always remains the owner of the output: `servingModelId=null`, and the model being trained is indicated only as a nullable `shadowModelId` and does not affect the items. After activation the same fragment looks like this:

```json
{
  "ranking": {
    "phase": "ACTIVE",
    "servingPolicy": "linucb-v1",
    "servingModelId": "uuid",
    "shadowModelId": null,
    "qualityScoreSource": "LINUCB_EXPECTED"
  }
}
```

Other endpoints:

- `POST /api/v1/waves/{queueId}/extend` — another 20 locally ranked items;
- `PATCH /api/v1/waves/{queueId}` — new temperature/mood for the unplayed tail;
- `GET /api/v1/waves/{queueId}` — restore after a reload;
- `POST /api/v1/waves/{queueId}/exclude` — exclude a candidate from the current queue without a global dislike.

## 5. Telemetry

`POST /api/v1/telemetry/events:batch` accepts at most 100 events or 128 KiB.

```json
{
  "schemaVersion": 1,
  "events": [
    {
      "clientEventId": "uuid",
      "sessionId": "uuid",
      "sequenceNo": 7,
      "videoId": "opaque-id",
      "type": "progress_tick",
      "occurredAt": "2026-08-01T18:42:10.123Z",
      "monotonicMs": 84211,
      "payload": {
        "positionSeconds": 31.2,
        "playedSeconds": 28.7,
        "effectiveDurationSeconds": 213,
        "durationSource": "PLAYER",
        "seekForwardSeconds": 0,
        "seekBackwardSeconds": 0,
        "bufferedSeconds": 2.1,
        "playerState": "PLAYING"
      }
    }
  ]
}
```

Response:

```json
{
  "accepted": 1,
  "duplicates": 0,
  "rejected": [],
  "serverTime": "2026-08-01T18:42:10.190Z"
}
```

One invalid event does not reject the whole batch; rejected contains the event ID and a code. An unknown `type` for a supported schema version is rejected.

## 6. Insights

- `GET /api/v1/insights/summary?period=30d`;
- `GET /api/v1/insights/learning-status`;
- `GET /api/v1/insights/pool`;
- `GET /api/v1/tracks/{videoId}/explanation?generationId=...`;
- `GET /api/v1/diagnostics/api-budget`.

`insights/pool` returns the state of the candidate graph and the freshness of waves: `graphEdges`, `graphCandidates`, `playableCandidates`, `candidatesByHop`, `recentOverlapPercent` (last 10 generations), `lastOverlapPercent`, `lastPoolSize`.

Explanation returns reason codes and numeric contributions, not a story generated by an LLM.

## 7. Managed publishing

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/managed-playlists/setup` | create or reconcile the three Tuner playlists after a preview |
| POST | `/api/v1/managed-playlists/{kind}/plan` | view the composition without external calls; `{"regenerate": true}` proposes a different variant |
| POST | `/api/v1/managed-playlists/{kind}/publish` | enqueue an idempotent job |
| POST | `/api/v1/managed-playlists/{kind}/reconcile` | check/adopt a CREATING, UNVERIFIED or CLEANUP_REQUIRED exact marker; for DELETED — `400` |
| DELETE | `/api/v1/managed-playlists/{kind}/setup-artifact` | after confirmation, delete your own playlist in any status, only an exact remote ID with a matching marker |
| GET | `/api/v1/publications/{id}` | state and verification |
| GET | `/api/v1/managed-playlists/{kind}/backups` | available snapshots |
| POST | `/api/v1/managed-playlists/{kind}/restore` | plan a manual restore |

Publish writes the preview saved on the manifest (a request without a body); protection against remote changes is performed on the server by comparing the remote hash with the last verified intermediate state (docs/03 §8, step 3) — a mismatch yields `409 REMOTE_CHANGED` without a write. `GET /api/v1/playlists` does not return manifests in DELETED status and includes `setupErrorCode` for unfinished setups.

`POST .../plan` returns **the list itself**, not only counters: a `tracks` array with `videoId`, `title`, `artists`, `familiarity` and `reasonCodes`, plus `randomSeed` and `generatedAt`. The list that was shown is saved on the manifest, so a repeated call returns the same one, and `publish` writes exactly that — otherwise something the user had not seen would be published. The body `{"regenerate": true}` replaces the proposal with a new candidate with a different seed.

Verification of a just-created playlist performs up to three reads with delays: YouTube responds with an incomplete payload until the playlist becomes readable. The order is checked against the saved desired hash, not against a freshly generated list; otherwise a repeated `reconcile` would always return `VERIFICATION_MISMATCH`.

The initial setup request also contains the accepted desired hash and the `playlist-gates-v2` results; before create, the backend saves a CREATING intent, creates a private playlist immediately with `effectiveTargetSize` video IDs, immediately registers the returned ID as UNVERIFIED and then verifies the full order. The setup response always returns `managedPlaylistId`, `status`, nullable `playlistId`, `configuredTargetSize`, `effectiveTargetSize`, gate reasons and exact pool counts. When the size is reduced, `TARGET_SIZE_REDUCED_FOR_POOL` is present; when the minimum of 25 cannot be reached, the backend responds without an external write:

```json
{
  "status": "SKIPPED_QUALITY",
  "reasonCode": "INSUFFICIENT_POOL",
  "configuredTargetSize": 60,
  "minimumPublishSize": 25,
  "requiredFamiliar": 18,
  "availableFamiliar": 14,
  "requiredDiscovery": 7,
  "availableDiscovery": 19
}
```

An incremental response may have `PARTIAL` and returns `remainingItemChanges`, `remainingEstimatedRequests`, `nextContinuationAfter` and the same `desiredHash`. Continuation is performed after 24 hours without requiring 15 new sessions, but only after repeated safety/ownership/hash/budget checks.

`DELETE .../setup-artifact` is called after explicit confirmation in the UI (a request without a body; the kind in the path unambiguously determines the manifest, and the ID/marker are taken from it). The backend calls `delete_managed_playlist`, records the fresh-read/delete in the call ledger and returns `{"kind":"...","status":"DELETED"}` on confirmed success or when the remote ID is absent (a local intent). A marker mismatch yields `403 PLAYLIST_NOT_MANAGED`; an ambiguous external response keeps CLEANUP_REQUIRED and returns a retryable integration error without an automatic retry. `POST .../setup` is idempotent: ACTIVE kinds are returned with `alreadyExisting: true` without an external call.

## 8. Error codes

| HTTP | Code | Meaning |
| ---: | --- | --- |
| 400 | `VALIDATION_FAILED` | invalid DTO |
| 401 | `YTM_AUTH_REQUIRED` | OAuth missing/expired |
| 403 | `PLAYLIST_NOT_MANAGED` | ownership guard |
| 409 | `PLAYLIST_UNVERIFIED` | regular publish is forbidden until verify/adopt or cleanup |
| 409 | `REMOTE_CHANGED` | optimistic concurrency conflict |
| 409 | `JOB_ALREADY_RUNNING` | dedupe/lease |
| 422 | `PLAYLIST_QUALITY_FAILED` | one or more versioned quality gates failed |
| 429 | `LOCAL_BUDGET_EXCEEDED` | internal cooldown/budget |
| 502 | `YTM_PARSE_ERROR` | incompatible external payload |
| 503 | `YTM_UNAVAILABLE` | transient external failure |
| 503 | `CIRCUIT_OPEN` | automatic calls are suspended |

No error response contains an OAuth endpoint body, headers or a stack trace.
