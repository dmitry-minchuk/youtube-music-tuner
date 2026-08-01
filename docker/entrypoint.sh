#!/bin/sh
# Safe entrypoint: apply forward migrations, then hand over to the server.
# Runs as UID 10001; never escalates and never chowns user-provided paths.
set -eu

DATA_DIR="${TUNER_DATA_DIR:-/data}"
PORT="${TUNER_PORT:-43127}"
BIND_HOST="${TUNER_BIND_HOST:-0.0.0.0}"

if [ ! -w "$DATA_DIR" ]; then
    echo "{\"level\":\"ERROR\",\"message\":\"data dir $DATA_DIR is not writable by uid $(id -u)\"}" >&2
    exit 1
fi

mkdir -p "$DATA_DIR/secrets" "$DATA_DIR/backups"
chmod 700 "$DATA_DIR/secrets" "$DATA_DIR/backups" 2>/dev/null || true

case "${1:-serve}" in
    serve)
        alembic upgrade head
        # No --log-config: the app installs its own redacting JSON handlers.
        exec uvicorn app.main:app \
            --host "$BIND_HOST" \
            --port "$PORT" \
            --workers 1 \
            --no-access-log
        ;;
    migrate)
        exec alembic upgrade head
        ;;
    *)
        exec "$@"
        ;;
esac
