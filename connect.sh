#!/usr/bin/env bash
# Connect Tuner to YouTube Music in one command.
#
# Opens a real browser window, waits for you to sign in, captures the request
# headers of a genuine /youtubei/ call, imports them into the container and
# shreds the intermediate file. The UI has a Connect button that does the same
# thing without a terminal; this exists for when the UI is not reachable.

set -euo pipefail

cd "$(dirname "$0")"

CONTAINER="${TUNER_CONTAINER:-youtube-music-tuner-tuner-1}"
VENV_PYTHON=".venv/bin/python"
HEADERS_FILE="secrets/browser-headers.txt"

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\033[31m%s\033[0m\n' "$1" >&2; exit 1; }

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
    fail "Container $CONTAINER is not running. Start it with: docker compose up -d"
fi

if [ ! -x "$VENV_PYTHON" ]; then
    fail "No virtualenv found. Create one with: python3 -m venv .venv && .venv/bin/pip install -e './backend[dev]' playwright"
fi

if ! "$VENV_PYTHON" -c "import playwright" >/dev/null 2>&1; then
    step "Installing Playwright (one-off)…"
    "$VENV_PYTHON" -m pip install -q playwright
    "$VENV_PYTHON" -m playwright install chromium
fi

step "Opening a browser window — sign in to YouTube Music there."
"$VENV_PYTHON" scripts/grab_browser_auth.py --timeout "${TUNER_SIGNIN_TIMEOUT:-420}" --out "$HEADERS_FILE"

[ -s "$HEADERS_FILE" ] || fail "No headers captured."

step "Importing into the container…"
if ! docker exec -i "$CONTAINER" python -m app.cli browser import - < "$HEADERS_FILE"; then
    rm -f "$HEADERS_FILE"
    fail "Import failed. The headers were discarded; try again."
fi

# The container has its own 0600 copy now; do not leave one lying around.
shred -u "$HEADERS_FILE" 2>/dev/null || rm -f "$HEADERS_FILE"

step "Done."
docker exec "$CONTAINER" python -m app.cli status
printf '\nOpen http://127.0.0.1:%s\n' "${APP_PORT:-43127}"
