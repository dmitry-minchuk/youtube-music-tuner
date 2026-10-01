# Development roadmap

## Delivery principle

Each phase ends with a working vertical slice. YouTube Music integration writes are added last, after the local player, telemetry and preview.

## Phase 0 — foundation

- Initialise Git and the basic quality tools.
- Create the backend/frontend skeleton and a multi-stage Dockerfile.
- Add Compose on `127.0.0.1:43127`, a named volume `/data` and a fixed runtime UID/GID `10001:10001`.
- Implement health endpoints, settings and Alembic bootstrap.
- Add CI/local commands lint, typecheck, unit test, build.

Definition of done: a clean machine launches the placeholder UI with one command; restart preserves the test row; the health check works.

## Phase 1 — read-only YouTube integration

- Implement `MusicCatalogPort` and the ytmusicapi adapter `1.12.1`.
- CLI OAuth device flow and secure secret files.
- Account, liked tracks, playlists, history sync.
- TTL caches, call ledger, cooldown/circuit breaker.
- UI Settings, Collection and Playlists in read-only mode.
- Contract fixtures and real read-only smoke.

Definition of done: the library is visible locally, reopening screens does not trigger external polling, an auth error is understandable.

## Phase 2 — own player and telemetry

- IFrame `PlayerPort` and visible Now Playing layout.
- Queue, play/pause/next/previous/seek/volume.
- IndexedDB event outbox, 15-second `progress_tick` and batch API.
- Raw events, session aggregator, reward v1.
- Insights baseline status.
- Fake player tests and browser smoke.

Definition of done: actual seconds correctly distinguish listen/seek/buffer; restart/reload does not lose confirmed events.

## Phase 3 — Wave v1

- Candidate refresh related/radio/mood with TTL.
- Rule-based cold-start ranker.
- Temperature quotas, mood context, diversity reranker.
- Wave/extend API and the main UI.
- Deterministic reason codes.

Definition of done: Wave plays at least 40 items from the local pool, temperature noticeably changes the composition without new external calls.

## Phase 4 — online learning

- Feature schema v1 and stored selection snapshots.
- LinUCB model/update/snapshot.
- Bootstrap thresholds, SHADOW from the 40th session, a clean rule-based baseline of 100 and activation safety gates.
- Offline replay metrics and model rollback.
- Insights comparison with baseline.

Definition of done: after sufficient real telemetry the model snapshot is activated automatically and reproducibly influences the order.

## Phase 5 — safe publication

- Crash-safe creation of three private managed playlists: CREATING intent, initial adaptive `video_ids`, UNVERIFIED registration, marker reconciliation and verification.
- Versioned playlist quality gates with rule/LinUCB `quality_expected`, adaptive target 25–60, exact pool diagnostics, desired list and a minimal convergent diff planner.
- Preview, backup, PARTIAL continuation without a new listening threshold and separate caps: 15 item changes, 15 mutating/17 total requests per playlist per 24-hour window.
- Fresh read/hash conflict/verification.
- Manual restore and auto-publish cooldown.
- Real write smoke on a separate test playlist.

Definition of done: the playlists appear in the official iPhone app; no playlist that is not Tuner's own can be changed by tests or UI.

## Phase 6 — hardening

- Daily backup + verified retention.
- 24-hour soak and API-budget audit.
- Secret scanning, CSP/Origin/CSRF/Host/DNS-rebinding tests, dependency scan.
- Empty/error/stale/accessibility polish.
- Updating the runbook with real commands and screenshots.

Definition of done: AT-01…AT-12 pass, the image version is recorded, auto-publish can be safely enabled.

## Phase 7 — selection quality (done 2026-08-01…03)

Everything described below has been implemented and verified on the live library; details are in ADR-004.

- **The feedback loop is closed.** Track and artist aggregates are populated at telemetry ingest. Before that they were empty, and the fatigue, recent-skip, novelty and rediscovery features were always zero.
- **The candidate pool became a graph.** Edges are no longer discarded by TTL, carry the distance from the favourites and are grown by a separate job every 6 hours. Link multiplicity (how many favourite tracks point at a candidate) is used as a feature.
- **Breadth before depth.** Unexpanded favourites are expanded before reachable candidates: a traversal greedy by support deepened a single cluster and left most of the library unrepresented.
- **Freshness as a constraint.** An adaptive novelty window, memory of the four most recent waves and of the artists in them, stochastic selection with a window that grows together with the pool, a limit on familiar rotation, quarantine for repeated skips.
- **Publishing stopped being blind.** Preview returns the composition, plays it and can be regenerated; the previewed list is published; any own playlist can be deleted.
- **Three infrastructure failures were fixed.** The scheduler tight loop, telemetry loss due to SQLite write locking, and the inability to verify a just-created playlist.

Measured effect: overlap of adjacent waves 95% → 0–25%, repetition of discovery tracks → 0%, playable candidates 119 → 3045, favourites coverage 8/51 → 61/61, distinct artists over six waves 78 → 161.

## First implementation slice

The recommended first PR/commit must contain only:

1. FastAPI `/health/live` and `/health/ready`;
2. an empty SQLite schema + migration;
3. a React shell with five sections and a status fetch;
4. Dockerfile/Compose on 43127;
5. unit smoke and container health test.

OAuth and `ytmusicapi` are added in the next isolated slice. This way Docker/UI problems are not mixed with the instability of the external integration.

## Deferred decisions

They do not block the MVP:

- whether a metadata embedding model is needed after 500+ sessions;
- whether to add LAN access with separate auth;
- whether additional managed playlists per mood are needed;
- whether to support a PWA/offline shell;
- whether to add an import from Yandex Music as a separate one-off tool.

Any of these extensions requires a separate BRD delta and ADR, rather than a hidden expansion of the current scope.
