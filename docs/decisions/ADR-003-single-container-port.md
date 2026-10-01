# ADR-003: one container and port 43127

- Status: Accepted
- Date: 2026-08-01
- Scope: deployment

## Context

The project is personal, must simply run in Docker and provide its own web-player UI. Microservices, Kubernetes, Redis and a separate model server give no benefit to a single user, but increase the risk of conflicts and maintenance.

Common dev ports are often taken by other projects. A high, memorable, but not well-known port is needed.

## Decision

- Build the React frontend in a multi-stage Dockerfile.
- Serve the static bundle and `/api` from a single FastAPI runtime container.
- Use a single Uvicorn worker, SQLite WAL, in-process persistent jobs and the named volume `tuner-data:/data`.
- Run the runtime with fixed UID/GID `10001:10001`; `/data` is prepared in the image with the same owner before the first mount.
- Listen on `43127` inside the container.
- Publish `127.0.0.1:43127:43127` by default.
- Allow overriding the host port via `APP_PORT`.

At the time of selection, 2026-08-01, there was no listener on TCP 43127 on the machine.

## Consequences

Pros: one build/run, same-origin UI/API, simple backups, no inter-service network, minimal resources.

Cons: one process combines HTTP and the scheduler; long jobs must be short/asynchronous, and heavy training in the future will require a worker. Scaling to multiple users is not supported.

## Guardrails

- job leases and idempotency are mandatory even with a single worker;
- the external bind must not be changed to LAN without a new security ADR;
- health/readiness do not depend on YouTube availability;
- the player `origin` is built from the actual browser origin, so changing `APP_PORT` is supported.
