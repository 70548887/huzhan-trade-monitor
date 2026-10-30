"""Background collection with one scheduler leader per data directory."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import settings
from .collector import collect
from .locking import file_lock

LOG = logging.getLogger("huzhan.scheduler")


class Scheduler:
    """Collect immediately, then every ten minutes, without blocking the web app.

    Workers elect a single leader with an OS lock. Followers periodically retry
    so they can take over if the current leader exits.
    """

    def __init__(
        self, interval_seconds: float = 600, retry_seconds: float = 5,
        collect_func: Callable[[], int] | None = None, lock_path: Path | None = None,
    ) -> None:
        if interval_seconds <= 0 or retry_seconds <= 0:
            raise ValueError("Scheduler intervals must be positive")
        self.interval_seconds = interval_seconds
        self.retry_seconds = retry_seconds
        self._collect = collect_func if collect_func is not None else collect
        self._lock_path = lock_path if lock_path is not None else settings.DATA_DIR / "scheduler.lock"
        self._stop = threading.Event()
        self._lifecycle = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> Scheduler:
        with self._lifecycle:
            if self._thread is not None:
                if self._thread.is_alive():
                    return self
                raise RuntimeError("A stopped scheduler cannot be restarted; create a new instance")
            self._thread = threading.Thread(
                target=self._run, name="huzhan-collector", daemon=True,
            )
            self._thread.start()
        return self

    def stop(self, timeout: float = 35) -> bool:
        """Request shutdown; an active collection retains its OS lock until done.

        A slow HTTP call may outlive this wait. Its daemon thread releases
        leadership only from its own finally after the collection returns.
        """
        self._stop.set()
        with self._lifecycle:
            worker = self._thread
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=max(0, timeout))
        return worker is None or not worker.is_alive()

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with file_lock(self._lock_path) as acquired:
                    if not acquired:
                        self._stop.wait(self.retry_seconds)
                        continue
                    LOG.info("Background collector acquired scheduler leadership")
                    self._run_leader()
            except Exception:
                LOG.exception("Background collector scheduler failed; retrying leadership")
                self._stop.wait(self.retry_seconds)

    def _run_leader(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._collect()
            except Exception:
                LOG.exception("Background collection raised an unexpected error")
            remaining = max(0, self.interval_seconds - (time.monotonic() - started))
            self._stop.wait(remaining)


def main() -> None:
    scheduler = Scheduler().start()
    try:
        while scheduler.is_alive():
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()


if __name__ == "__main__":
    main()
