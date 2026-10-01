# Docker and operations

## 1. Local endpoint

The application port inside the container and the default host port: `43127`. At the time of the architecture check on 2026-08-01, the port was not in use on the developer's machine.

```text
http://127.0.0.1:43127
```

This is deliberately not one of the common 3000, 5000, 8000, 8080, 8888 or 9000. Binding only to `127.0.0.1` rules out accidental access from the LAN.

## 2. Target Compose configuration

Planned form:

```yaml
services:
  tuner:
    build: .
    restart: unless-stopped
    ports:
      - "127.0.0.1:${APP_PORT:-43127}:43127"
    environment:
      TUNER_BIND_HOST: "0.0.0.0"
      TUNER_PORT: "43127"
      TUNER_PUBLIC_PORT: "${APP_PORT:-43127}"
      TUNER_DATA_DIR: "/data"
      TZ: "Europe/Warsaw"
    volumes:
      - tuner-data:/data
    healthcheck:
      test: ["CMD", "python", "-m", "app.healthcheck"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

volumes:
  tuner-data:
```

`0.0.0.0` inside the container does not make the service public: the host-side mapping remains loopback-only. The default uses a named volume rather than a `./data` bind mount, so that the fixed non-root user can write on the first run without a host-specific `chown`. This initial population of an empty volume with the prepared contents of the mount path is [documented Docker volumes behaviour](https://docs.docker.com/engine/storage/volumes/).

## 3. Dockerfile

Multi-stage:

1. `frontend-build`: Node 20 LTS, clean install from the lockfile, typecheck, tests, `vite build`.
2. `backend-build`: install Python dependencies into a virtual environment/wheel layer.
3. `runtime`: slim Python image, non-root user, frontend dist and backend package, without Node/npm/build tools.

Runtime image:

- runs as the fixed UID/GID `10001:10001`, without root;
- contains only the necessary CA certificates and timezone data;
- before `USER 10001:10001`, the image creates `/data`, `/data/secrets`, `/data/backups` with owner `10001:10001` and modes `0700`; on the first mount the named volume preserves the prepared ownership/permissions;
- runs the migration command before Uvicorn through a safe entrypoint;
- uses a single Uvicorn worker.

## 4. Planned commands

```bash
# Build and run
docker compose up --build -d

# Check
curl --fail http://127.0.0.1:43127/health/ready

# Logs without follow
docker compose logs --tail=200 tuner

# Safely import the Google client JSON; the CLI writes the file as UID 10001 with mode 0600
docker compose exec -T tuner python -m app.cli credentials import - < client_secret.json

# OAuth device flow (does not work with a self-made client — see ADR-001; use browser headers)
docker compose exec tuner python -m app.cli auth

# Connect through the browser's cookie headers: the script puts the file in /data/secrets
./connect.sh

# Manual backup
docker compose exec tuner python -m app.cli backup

# Copy an already verified backup from the volume to the host
docker compose cp tuner:/data/backups/2026-08-01T12-00-00Z ./backups/2026-08-01T12-00-00Z

# Stop without deleting data
docker compose down
```

`docker compose down -v` will delete the named volume with the entire DB, OAuth and backup copies, and is therefore forbidden as an ordinary command. A full reset is performed by a separate CLI procedure after an externally verified copy and exact confirmation of the volume name.

## 5. Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_PORT` | `43127` | host port Compose substitution |
| `TUNER_BIND_HOST` | `0.0.0.0` | listen address only inside the container; the host mapping remains loopback |
| `TUNER_PORT` | `43127` | container listen port |
| `TUNER_PUBLIC_PORT` | `${APP_PORT:-43127}` | allowed port in the browser Host/Origin and the IFrame origin |
| `TUNER_DATA_DIR` | `/data` | DB, backups, secrets |
| `TZ` | `Europe/Warsaw` | display of local time; the DB is UTC regardless |
| `TUNER_LOG_LEVEL` | `INFO` | log level |
| `TUNER_AUTO_PUBLISH` | `false` until onboarding | autopublish after an explicit enable |
| `TUNER_RAW_EVENT_RETENTION_DAYS` | `180` | retention |

Runtime identity is not configured through environment variables: UID/GID `10001:10001` are part of the image contract and are verified by a container test. The OAuth client and token are not passed through environment variables. They are read from the files `/data/secrets/client.json` and `/data/secrets/oauth.json`.

A bind mount is allowed only as an explicit advanced override. Before starting, the owner of the host directory must prepare the directory for UID/GID `10001:10001` and verify writing with a one-off container test; Tuner does not run a root entrypoint and does not perform an automatic recursive `chown` of a user-supplied path.

## 6. First-run procedure

1. Create `.env` only if `APP_PORT` needs to be changed.
2. `docker compose up --build -d`.
3. Open `/settings`; readiness must be green, YouTube status — not connected.
4. Import the Google client credentials through `credentials import -`; the helper validates the JSON and writes it inside the named volume with mode `0600`.
5. Run `python -m app.cli auth` and the device flow.
6. Run a read-only initial sync.
7. Review the three managed playlist plans and only then create them.
8. Autopublish stays off by default until a successful test publish/verify.

## 7. Health and restart

- Liveness does not call YouTube/Google.
- Readiness checks that the DB opens, the schema version and that `/data` is writable; an external service does not block readiness.
- After a restart the scheduler releases expired leases. A CREATING/UNVERIFIED setup first reconciles by instance marker; a publish in WRITING/VERIFYING first does a fresh remote read and then becomes COMPLETE, PARTIAL with a continuation into the next 24-hour window, or FAILED. Neither create nor add/move is blindly repeated.
- Compose `restart: unless-stopped` suits a personal always-on mode.
- App startup does not run a full sync immediately if the last successful sync is still within its TTL.

### What to check when something goes wrong

- **Size of `jobs`.** Normal is hundreds of rows. Thousands mean that periodic work is being re-enqueued without a pause (docs/02 section 8): once it grew to 2.5 million rows and a 944 MB DB file. Diagnosis — the number of tasks created per minute.
- **`database is locked` in the logs.** An external call holds the single SQLite write lock; telemetry is lost as a result. `busy_timeout` = 30 s, and long operations commit intermediate state.
- **Discovery budget consumption.** `GET /api/v1/diagnostics/api-budget` shows the calls used; exhaustion is normal on days of active graph expansion, not a failure.
- **Favourites coverage.** `GET /api/v1/insights/pool` shows the pool size and the distribution by distance. A pool that does not grow means that graph expansion is not running.

## 8. Backup

Before backup:

1. open the SQLite online backup API or run `VACUUM INTO` into a consistent file inside the named volume;
2. copy the secret files separately with permissions `0600`;
3. create a manifest with the app/schema version, timestamp and SHA-256;
4. save to `/data/backups/YYYY-MM-DDTHH-mm-ssZ/`;
5. check that the backup database opens, using an integrity check command.

Automatic daily backups are kept for 14 days. Playlist snapshots inside the DB do not replace a backup of the whole DB.

Retention also bounds the service tables: successful jobs live 7 days, failed ones — 30, and candidate graph edges are deleted only if they have not been updated in a year (docs/07 section 8). The graph is accumulated knowledge, not a cache, so the main growth of the DB file comes from it: on the order of a thousand edges per day during active expansion.

A copy is considered external only after `docker compose cp` into a host directory and a repeated manifest/checksum check on the host. A backup that lies in the same named volume does not protect against deletion of the volume.

## 9. Restore

- Stop Compose without `-v`.
- Check the checksum and SQLite integrity of the external backup copy.
- Run a one-off restore CLI in the same image: inside the named volume it moves the current `/data/tuner.db` into a timestamped quarantine, imports the backup and sets owner/mode `10001:10001`/`0600`.
- Start the main container and apply only forward migrations.
- Check readiness, account identity and the managed playlist manifest before enabling jobs.

OAuth does not have to be restored: it is safer to go through connect again if the origin of the backup is unclear.

## 10. Upgrade

1. Create and verify a backup.
2. Build a new image with lockfiles.
3. Run unit/contract tests.
4. Run the migration on a copy of the DB.
5. Update the container.
6. Check health, read-only sync and player smoke.
7. Leave auto-publish off until a smoke test of a managed test playlist when the integration layer changes.

Rolling back the image is allowed only if the new migration is backward-compatible; otherwise the verified pre-upgrade backup is restored.

## 11. Changing the port

In `.env`:

```dotenv
APP_PORT=43128
```

After the change, the allowed `origin` for the IFrame Player is updated, while the UI/API keep using same-origin relative URLs. Before choosing a port, it is checked with `lsof -nP -iTCP:<port> -sTCP:LISTEN`.
