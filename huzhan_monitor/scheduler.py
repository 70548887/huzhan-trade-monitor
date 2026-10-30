"""Run the collector every ten minutes in the Docker collector service."""
from __future__ import annotations

import time

from .collector import collect


def main() -> None:
    interval_seconds = 600
    while True:
        started = time.monotonic()
        collect()
        remaining = interval_seconds - (time.monotonic() - started)
        time.sleep(max(0, remaining))


if __name__ == "__main__":
    main()
