# Security, privacy and risks

## 1. Usage model

The application is intended for a single owner, runs on a trusted home computer and is reachable only via loopback. This reduces the attack surface but does not remove the need to protect the OAuth token and to validate all input.

The application is not intended for public hosting, sale, transfer to other users, or circumventing YouTube restrictions.

## 2. Sensitive data

| Data | Location | Protection |
| --- | --- | --- |
| OAuth refresh/access token | `/data/secrets/oauth.json` | `0600`, not in Git/logs/backup without explicit protection |
| Google client secret | `/data/secrets/client.json` | `0600`, import only via CLI, not env, not UI |
| YouTube Music browser cookies | `/data/secrets/browser.json` | `0600`, import via UI form or CLI, redacted in logs, deleted on disconnect |
| History/telemetry | SQLite | loopback-only app, local retention/delete |
| Playlist IDs/video IDs | SQLite | treated as personal metadata, not published |
| Database backups | `/data/backups` | local permissions, checksum, retention |

In the MVP, DB encryption by the application is not added: it would create its own key management. FileVault is assumed to be enabled on macOS to protect the disk. If `/data` is moved to a NAS or a shared disk, the security model must be revisited.

## 3. Web security

- the host publishes only on `127.0.0.1`;
- CORS is disabled;
- middleware ahead of routing accepts only a `Host` of `127.0.0.1`, `localhost`, `[::1]` with the configured port, blocking DNS rebinding already in the MVP;
- a mutation checks the exact `Origin`, an HttpOnly `SameSite=Strict` local session cookie and the associated `X-CSRF-Token`;
- the connection form in Settings accepts browser cookies and is protected by the same guards; the value is not returned in a response, is not logged and is stored in a `0600` file. The Google client secret is still not accepted through the UI (docs/03 section 2);
- CSP allows own resources and the required `script-src`/`frame-src` YouTube IFrame domains, without `unsafe-eval`;
- the iframe gets the minimum required permissions;
- the API does not accept an arbitrary URL to fetch, only opaque IDs;
- response headers: `X-Content-Type-Options`, `Referrer-Policy: strict-origin-when-cross-origin`, `frame-ancestors 'self'` for the UI; the referrer policy must not hide the origin from the YouTube player;
- diagnostics does not return filesystem secrets or stack traces.

If LAN access is needed later, an HTTPS reverse proxy, application authentication, a trusted-host allowlist and a new threat review are mandatory. Simply changing the bind to `0.0.0.0` is prohibited.

## 4. Secret handling

- `.gitignore` excludes `data/`, `secrets/`, OAuth/cookie JSON and `.env`.
- The log filter redacts the keys `authorization`, `cookie`, `client_secret`, `access_token`, `refresh_token`, `visitorData` regardless of case.
- Integration exceptions are logged by a safe code and request ID, without the full request/response.
- OAuth files are read at startup/refresh and are not passed to the frontend.
- An OAuth backup is created only when explicitly configured; an ordinary backup may exclude `/data/secrets`.
- Disconnect deletes the token file, but the UI also provides a link/instructions for revoking access in the Google Account.

## 5. Protection of remote data

The greatest application-level risk is to corrupt an existing playlist. Safeguards:

- a regular publish only for an ACTIVE ID from `managed_playlists`;
- the create intent is saved before the external call, the returned ID is stored as UNVERIFIED until verification; on restart the marker is reconciled instead of creating a duplicate;
- a match of the remote ownership marker and the local instance UUID; an UNVERIFIED playlist can only go through verify/adopt or be deleted on explicit confirmation;
- deletion is allowed for UNVERIFIED, CLEANUP_REQUIRED and ACTIVE — that is, for the listener's own playlists — but each time by exact ID + a fresh marker check. The guarantee lies not in the status but in the marker: a playlist not created by Tuner is unreachable in any status. The earlier ban on deleting ACTIVE protected nothing and only deprived the listener of the ability to remove a playlist they did not like;
- private visibility on creation;
- playlist quality gates before any create/publish;
- a fresh read and an optimistic `content_hash` before writing;
- a full before-snapshot;
- preview diff;
- the initial create in a single confirmed call with `effective_target_size` IDs and an immediate full verification; afterwards at most 15 item changes and 15 mutating requests per window;
- a per-playlist endpoint cap of 17 (fresh read + up to 15 mutations + verify) and a global automatic cap of 51 requests/day for three playlists;
- a PARTIAL publication continues the immutable desired hash without a new listening threshold, but waits 24 hours and passes the safety/ownership/hash/budget checks again;
- a state machine and a verification read;
- manual restore, with no automatic blind rollback;
- auto-publish is off until a successful test cycle.

## 6. Unofficial API

`ytmusicapi` explicitly positions itself as an unofficial API and replicates the requests of the YouTube Music client. This means:

- internal endpoints and payloads may change without notice;
- individual methods may temporarily stop working;
- OAuth/cookie behaviour may change;
- there is no official Google support;
- excessive/atypical requests may lead to throttling or other account restrictions.

For a personal project the risk is accepted. Mitigation: pinned version, adapter boundary, low call budget, exponential backoff, circuit breaker, fixtures, a real read-only smoke test, and no bulk operations.

## 7. YouTube rules and playback

The official YouTube API policies require the use of documented APIs and impose requirements on the embedded player. At the same time, `ytmusicapi` is based on an undocumented interface. The non-commercial/personal nature reduces the scale of the consequences but does not make the integration officially supported.

Therefore the architecture:

- explicitly documents its experimental nature;
- uses the official IFrame API for playback;
- keeps the player visible and of sufficient size;
- provides a setting to pause on `document.visibilityState=hidden`; it is off by default by the direct decision of the installation owner, because the browser reports `hidden` even when the window is merely covered, which made the music cut off on every application switch. The residual policy risk is accepted by the owner and recorded in docs/04 section 1;
- does not download/store media;
- does not hide mandatory controls/branding;
- does not build separate derived YouTube aggregate metrics for transfer to third parties;
- does not provide the service to other users.

Before any public hosting or commercialisation, development must be stopped and the policy/legal review passed anew; the current architecture is not designed for this.

## 8. Privacy

- Raw telemetry does not leave the computer.
- No Sentry, Google Analytics, external LLM or cloud vector DB.
- The UI shows which events are collected and how they are interpreted.
- The time-of-day context can be switched off; the microphone, camera, geolocation and contacts are not used.
- The user may delete raw telemetry separately or perform a full reset.
- The export by default contains aggregates without OAuth and external private IDs; a full debug export requires additional confirmation.

## 9. Risk register

| Risk | Likelihood | Impact | Mitigations | Residual risk |
| --- | --- | --- | --- | --- |
| Change of internal YTM payloads | high | high | adapter, pin, tests, cached fallback | medium |
| OAuth revocation/expiry | medium | medium | clear reconnect, stop writes | low |
| Damage to a managed playlist | low | high | marker, hash, backup, diff, verify | low-medium |
| API spam/throttling | low | high | ledger, TTL, daily budgets, circuit | low |
| Token leak via Git/log | low | high | ignore, file secret, redaction tests | low |
| Misinterpretation of a skip | medium | medium | explicit next only, neutral unknown, raw events | low-medium |
| The model got stuck on artists | medium | medium | diversity gates, artist-level memory, monitoring, model rollback | low |
| The pool reflects only part of the taste, waves sound the same | **materialised** | medium | breadth before depth when traversing the graph: unexpanded favourites are expanded before reachable candidates; a root coverage metric | low |
| The scheduler goes into a tight loop and bloats the DB | **materialised** | high | backoff on any completed attempt, circuit only for external jobs, a limit on consecutive runs, retention of successful jobs | low |
| A long external call holds the SQLite write lock and loses telemetry | **materialised** | medium | commit right after the ID is received, busy_timeout 30 s | low |
| Blind publishing: the user does not see the playlist's contents | **materialised** | medium | Preview returns the tracks and plays them, Publish writes the previewed list, the button is blocked until previewed | low |
| IFrame track unavailable/ad | medium | low-medium | neutral error, skip candidate; do not circumvent the restrictions | medium |
| Playback with a hidden tab diverges from a strict reading of the policy | medium | low | a setting with an explicit description, off by the owner's decision; the player stays visible and unmasked | accepted by the owner |
| Public/LAN exposure by mistake | low | high | hardcoded loopback Compose mapping, startup warning | low |
| The project stopped working after an update | medium | medium | backups, pinned images/deps, upgrade runbook | low-medium |

## 10. Incident actions

### Suspected OAuth leak

1. Stop the container.
2. Revoke the application's access in the Google Account.
3. Delete `/data/secrets/oauth.json`.
4. Check the Git history and logs for secret patterns.
5. If the client secret leaked, delete the OAuth client and create a new one.
6. Resume work only after verification.

### Unexpected playlist change

1. Disable auto-publish/circuit.
2. Save the current remote state and the call ledger.
3. Compare the publication plan/applied operations/verification.
4. Restore the chosen snapshot manually after a preview.
5. Do not repeat publish until the cause is fixed and a test-playlist smoke passes.
