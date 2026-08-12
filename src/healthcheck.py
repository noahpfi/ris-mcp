"""Container healthcheck. Exits 0 when the HTTP transport answers.

A module rather than a curl line in the Dockerfile so the port comes from the
same config as the server and cannot drift out of sync. The image carries no
curl or wget, which is deliberate.
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request

from . import config
from .http_app import HEALTH_PATH


def main() -> int:
    url = f"http://127.0.0.1:{config.PORT}{HEALTH_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=4) as response:
            return 0 if response.status == 200 else 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"healthcheck failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
