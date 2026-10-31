"""Background collection with one scheduler leader per data directory."""
from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import preferences, settings
from .collector import collect
from .locking import file_lock

LOG = logging.getLogger("huzhan.scheduler")


class Scheduler:
    """Collect immediately, then follow the persisted interval in the background.

    Workers elect a single leader with an OS lock. Followers periodically retry
    so they can take over if the current leader exits.
    """

    def __init__(
        self, interval_seconds: float | None = None, retry_seconds: float = 5,
        collect_func: Callable[[], int] | None = None, lock_path: Path | None = None,
        interval_provider: Callable[[], float] | None = None,
    ) -> None:
        if (interval_seconds is not None and interval_seconds <= 0) or retry_seconds <= 0:
            raise ValueError("Scheduler intervals must be positive")
        if interval_seconds is not None and interval_provider is not None:
            raise ValueError("Choose a fixed interval or an interval provider, not both")
        self.interval_seconds = interval_seconds
        self.retry_seconds = retry_seconds
        # Providers return seconds. The default reads persisted minutes without
        # requiring a web-process restart after a setting changes.
        self._interval_provider = interval_provider or (
            (lambda: preferences.get_interval_minutes() * 60)
            if interval_seconds is None else (lambda: interval_seconds)
        )
        self._last_interval = interval_seconds or preferences.DEFAULT_INTERVAL_MINUTES * 60
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
            self._wait_for_next_collection(started)

    def _read_interval(self) -> float:
        try:
            value = self._interval_provider()
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError("Collection interval provider must return positive seconds")
            self._last_interval = value
        except Exception:
            # A transient storage error must not trigger a tight retry/collect
            # loop. Keep the last valid interval until a later read succeeds.
            LOG.exception("Unable to read collection interval; keeping last valid value")
        return self._last_interval

    def _wait_for_next_collection(self, started: float) -> None:
        while not self._stop.is_set():
            remaining = started + self._read_interval() - time.monotonic()
            if remaining <= 0:
                return
            # Recalculate from this collection's original start, so extending
            # delays the next run and shortening an elapsed period runs once.
            self._stop.wait(min(remaining, 5, self.retry_seconds))


def main() -> None:
    preferences.initialize()
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
