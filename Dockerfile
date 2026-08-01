# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------
# 1. Frontend build — Node is present only in this stage.
# --------------------------------------------------------------------------
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npx tsc -b && npx vite build

# --------------------------------------------------------------------------
# 2. Backend dependencies — isolated virtual environment.
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS backend-build
WORKDIR /build

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir .

# --------------------------------------------------------------------------
# 3. Runtime — slim image, no Node, no build tools, non-root.
# --------------------------------------------------------------------------
FROM python:3.13-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    TUNER_DATA_DIR=/data \
    TUNER_PORT=43127 \
    TUNER_BIND_HOST=0.0.0.0

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates tzdata; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 10001 tuner; \
    useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home --shell /usr/sbin/nologin tuner

COPY --from=backend-build /opt/venv /opt/venv

WORKDIR /app
COPY --chown=10001:10001 backend/app ./app
COPY --chown=10001:10001 backend/alembic ./alembic
COPY --chown=10001:10001 backend/alembic.ini ./alembic.ini
COPY --chown=10001:10001 docker/entrypoint.sh /usr/local/bin/tuner-entrypoint
COPY --from=frontend-build --chown=10001:10001 /build/dist ./app/static

# Prepare the data tree inside the image so a fresh named volume inherits
# the right owner and mode on first mount (docs/09 sections 2-3).
RUN set -eux; \
    mkdir -p /data /data/secrets /data/backups; \
    chown -R 10001:10001 /data; \
    chmod 700 /data /data/secrets /data/backups; \
    chmod 755 /usr/local/bin/tuner-entrypoint

USER 10001:10001
EXPOSE 43127

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-m", "app.healthcheck"]

ENTRYPOINT ["tuner-entrypoint"]
CMD ["serve"]
