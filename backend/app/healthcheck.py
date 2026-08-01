"""Container healthcheck entrypoint: ``python -m app.healthcheck``.

Exits 0 when the app reports ready, 1 otherwise. Uses the loopback address
inside the container and never contacts external services.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

from app.settings import get_settings


def main() -> int:
    settings = get_settings()
    url = f"http://127.0.0.1:{settings.port}/health/ready"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:  # noqa: S310 - fixed loopback URL
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, TimeoutError, OSError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
