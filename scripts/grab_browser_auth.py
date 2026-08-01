"""Open a real browser window, wait for the user to sign in, capture headers.

Copying request headers out of DevTools is fiddly, so this does the same
thing hands-off: it opens music.youtube.com in a visible Chromium window,
waits until the session cookies appear, captures the exact headers of a real
/youtubei/ request and writes them where the CLI can import them.

The cookies never leave this machine: they go straight into the container's
`/data/secrets/browser.json`, the same file the manual path produces.

Usage:
    .venv/bin/python scripts/grab_browser_auth.py [--timeout 300] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import Request, sync_playwright

MUSIC_URL = "https://music.youtube.com/"
# __Secure-3PAPISID is the cookie ytmusicapi hashes into Authorization.
REQUIRED_COOKIES = ("__Secure-3PAPISID", "SID")
INTERESTING_HEADERS = (
    # Without authorization ytmusicapi mistakes the file for an OAuth token;
    # the CLI can rebuild it from the cookie, but keep the real one when seen.
    "authorization",
    "cookie",
    "user-agent",
    "accept",
    "accept-language",
    "content-type",
    "origin",
    "referer",
    "x-goog-authuser",
    "x-goog-visitor-id",
    "x-origin",
    "x-youtube-client-name",
    "x-youtube-client-version",
)


def _signed_in(cookies: list[dict]) -> bool:
    names = {cookie["name"] for cookie in cookies}
    return all(required in names for required in REQUIRED_COOKIES)


def capture(timeout_seconds: int, out_path: Path) -> int:
    captured: dict[str, str] = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        def on_request(request: Request) -> None:
            # Only a real API call carries the full header set we need.
            if "/youtubei/v1/" not in request.url or request.method != "POST":
                return
            headers = request.all_headers()
            if "cookie" not in headers:
                return
            captured.clear()
            captured.update(
                {name: value for name, value in headers.items() if name in INTERESTING_HEADERS}
            )

        context.on("request", on_request)
        page = context.new_page()
        page.goto(MUSIC_URL, wait_until="domcontentloaded")

        print("A browser window is open. Sign in to YouTube Music there.", flush=True)
        print("Waiting for the session…", flush=True)

        deadline = time.monotonic() + timeout_seconds
        signed_in_at: float | None = None

        while time.monotonic() < deadline:
            cookies = context.cookies()
            if _signed_in(cookies):
                if signed_in_at is None:
                    signed_in_at = time.monotonic()
                    print("Signed in. Capturing a real request…", flush=True)
                    # Nudge the app into issuing an API call we can observe.
                    try:
                        page.goto(MUSIC_URL + "library", wait_until="domcontentloaded")
                    except Exception:  # noqa: BLE001 - navigation is best effort
                        pass
                if captured:
                    break
                # Give the page a moment to fire /youtubei/ traffic.
                if time.monotonic() - signed_in_at > 25:
                    print("No API request seen; falling back to cookies only.", flush=True)
                    cookie_header = "; ".join(
                        f"{c['name']}={c['value']}"
                        for c in cookies
                        if c["domain"].endswith("youtube.com")
                    )
                    captured.update(
                        {
                            "cookie": cookie_header,
                            "user-agent": page.evaluate("navigator.userAgent"),
                            "accept-language": "en-US,en;q=0.9",
                            "origin": "https://music.youtube.com",
                            "x-goog-authuser": "0",
                        }
                    )
                    break
            time.sleep(2)

        browser.close()

    if not captured:
        print("Timed out before a signed-in session appeared.", file=sys.stderr)
        return 1

    raw = "\n".join(f"{name}: {value}" for name, value in sorted(captured.items()))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(raw, encoding="utf-8")
    out_path.chmod(0o600)

    summary = {
        "status": "ok",
        "headersCaptured": sorted(captured),
        "cookieLength": len(captured.get("cookie", "")),
        "out": str(out_path),
    }
    print(json.dumps(summary, indent=2), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for sign-in")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("secrets/browser-headers.txt"),
        help="where to write the captured header block",
    )
    args = parser.parse_args()
    return capture(args.timeout, args.out)


if __name__ == "__main__":
    sys.exit(main())
