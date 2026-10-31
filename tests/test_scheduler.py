from __future__ import annotations

import tempfile
import threading
import unittest
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from huzhan_monitor.locking import file_lock
from huzhan_monitor.scheduler import Scheduler


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.lock_path = Path(self.directory.name) / "scheduler.lock"
        self.schedulers = []

    def tearDown(self):
        for scheduler in self.schedulers:
            scheduler.stop(timeout=2)
        self.directory.cleanup()

    def scheduler(self, callback, interval=0.05):
        scheduler = Scheduler(
            interval_seconds=interval, retry_seconds=0.01,
            collect_func=callback, lock_path=self.lock_path,
        )
        self.schedulers.append(scheduler)
        return scheduler

    def test_two_schedulers_elect_one_leader_and_follower_takes_over(self):
        entered = threading.Event()
        release = threading.Event()
        follower_collected = threading.Event()

        def first_collect():
            entered.set()
            release.wait(2)
            return 0

        first = self.scheduler(first_collect, interval=10).start()
        self.assertTrue(entered.wait(1))
        second = self.scheduler(lambda: follower_collected.set() or 0, interval=10).start()
        self.assertFalse(follower_collected.wait(0.08))
        self.assertFalse(first.stop(timeout=0))
        self.assertFalse(follower_collected.wait(0.04))
        release.set()
        self.assertTrue(first.stop(timeout=1))
        self.assertTrue(follower_collected.wait(1))
        self.assertTrue(second.stop(timeout=1))

    def test_stop_prevents_another_collection(self):
        collected = threading.Event()
        duplicate = threading.Event()
        count = 0

        def callback():
            nonlocal count
            count += 1
            (collected if count == 1 else duplicate).set()
            return 0

        scheduler = self.scheduler(callback, interval=0.3).start()
        self.assertTrue(collected.wait(1))
        self.assertTrue(scheduler.stop(timeout=1))
        self.assertFalse(duplicate.wait(0.35))
        self.assertEqual(count, 1)

    def test_unexpected_collection_error_does_not_stop_next_run(self):
        recovered = threading.Event()
        count = 0

        def callback():
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("intentional test failure")
            recovered.set()
            return 0

        scheduler = self.scheduler(callback)
        with self.assertLogs("huzhan.scheduler", level="ERROR") as logged:
            scheduler.start()
            self.assertTrue(recovered.wait(1))
            self.assertTrue(scheduler.stop(timeout=1))
        self.assertIn("intentional test failure", "\n".join(logged.output))

    def test_start_is_idempotent_while_running(self):
        collected = threading.Event()
        scheduler = self.scheduler(lambda: collected.set() or 0, interval=10)
        self.assertIs(scheduler.start(), scheduler)
        self.assertIs(scheduler.start(), scheduler)
        self.assertTrue(collected.wait(1))
        self.assertTrue(scheduler.stop(timeout=1))

    def test_shorter_interval_takes_effect_without_restarting(self):
        interval = [1.0]
        first, second = threading.Event(), threading.Event()
        count = [0]
        def callback():
            count[0] += 1
            (first if count[0] == 1 else second).set()
            return 0
        scheduler = Scheduler(interval_provider=lambda: interval[0], retry_seconds=0.01,
                              collect_func=callback, lock_path=self.lock_path)
        self.schedulers.append(scheduler)
        scheduler.start()
        self.assertTrue(first.wait(1))
        interval[0] = 0.06
        self.assertTrue(second.wait(0.5))
        self.assertTrue(scheduler.stop(timeout=1))

    def test_longer_interval_postpones_the_next_run(self):
        interval = [0.2]
        first, second = threading.Event(), threading.Event()
        count = [0]
        def callback():
            count[0] += 1
            (first if count[0] == 1 else second).set()
            return 0
        scheduler = Scheduler(interval_provider=lambda: interval[0], retry_seconds=0.01,
                              collect_func=callback, lock_path=self.lock_path)
        self.schedulers.append(scheduler)
        scheduler.start()
        self.assertTrue(first.wait(1))
        interval[0] = 0.6
        self.assertFalse(second.wait(0.3))
        self.assertTrue(second.wait(1))
        self.assertTrue(scheduler.stop(timeout=1))

    def test_file_lock_busy_and_released(self):
        with file_lock(self.lock_path) as first:
            self.assertTrue(first)
            with file_lock(self.lock_path) as second:
                self.assertFalse(second)
        with file_lock(self.lock_path) as reacquired:
            self.assertTrue(reacquired)

    def test_file_lock_excludes_another_process(self):
        child_code = (
            "from pathlib import Path; import sys; "
            "from huzhan_monitor.locking import file_lock; "
            "scope=file_lock(Path(sys.argv[1])); "
            "locked=scope.__enter__(); print(int(locked)); "
            "scope.__exit__(None,None,None)"
        )

        def child_result():
            return subprocess.run(
                [sys.executable, "-c", child_code, str(self.lock_path)],
                capture_output=True, text=True, check=True, timeout=5,
            ).stdout.strip()

        with file_lock(self.lock_path) as acquired:
            self.assertTrue(acquired)
            self.assertEqual(child_result(), "0")
        self.assertEqual(child_result(), "1")

    def test_file_open_permission_error_is_not_reported_as_contention(self):
        with patch.object(Path, "open", side_effect=PermissionError("no permission")):
            with self.assertRaises(PermissionError):
                with file_lock(self.lock_path):
                    self.fail("a permission error cannot yield a lock result")


if __name__ == "__main__":
    unittest.main()
