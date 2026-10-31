from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from huzhan_monitor import cadence, db, preferences, reports, settings


class PreferencesTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        folder = Path(self.directory.name)
        self.configuration = patch.multiple(settings, DATA_DIR=folder, DB_PATH=folder / "monitor.sqlite3")
        self.configuration.start()
        db.initialize()

    def tearDown(self):
        self.configuration.stop()
        self.directory.cleanup()

    def test_existing_database_gets_default_without_losing_records(self):
        run_id = db.begin_run(settings.stamp())
        self.assertEqual(preferences.get_config(), {"interval_minutes": 10, "updated_at": None})
        preferences.initialize()
        preferences.initialize()
        with db.connect() as conn:
            self.assertIsNotNone(conn.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone())
        self.assertEqual(preferences.get_history(), [])

    def test_change_is_persisted_and_unchanged_value_does_not_duplicate_history(self):
        first = preferences.set_interval(60, "admin")
        preferences.initialize()
        self.assertEqual(preferences.get_config(), first)
        self.assertEqual(preferences.get_interval_minutes(), 60)
        self.assertEqual(preferences.set_interval(60, "admin"), first)
        history = preferences.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["interval_minutes"], 60)
        self.assertEqual(history[0]["changed_by"], "admin")

    def test_interval_range_and_type_are_validated_before_writes(self):
        for invalid in (0, -1, 1441, True, False, 1.5, "60", None):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                preferences.set_interval(invalid, "admin")
        self.assertEqual(preferences.get_interval_minutes(), 10)
        for valid in (1, 1440):
            self.assertEqual(preferences.set_interval(valid, "admin")["interval_minutes"], valid)

    def test_hourly_runs_do_not_create_fifteen_minute_outages(self):
        start = datetime(2026, 1, 1, 0, tzinfo=settings.BEIJING)
        with patch.object(settings, "stamp", return_value=start.isoformat()):
            preferences.set_interval(60, "admin")
        with db.connect() as conn:
            for hour in range(24):
                at = (start + timedelta(hours=hour)).isoformat()
                conn.execute("INSERT INTO runs(started_at,finished_at,status) VALUES(?,?,'success')", (at, at))
            conn.execute("INSERT INTO metadata(key,value) VALUES('trial_started',?)", (start.isoformat(),))
            conn.execute("INSERT INTO metadata(key,value) VALUES('baseline_completed',?)", (start.isoformat(),))
        now = start + timedelta(hours=24)
        self.assertEqual(reports.coverage(start.date(), now)["gaps"], [])
        trial = db.evaluate_trial(now)
        self.assertTrue(trial["passed"])
        self.assertEqual(trial["expected_count"], 24)

    def test_late_frequency_extension_does_not_erase_existing_outage(self):
        start = datetime(2026, 1, 1, 12, tzinfo=settings.BEIJING)
        early = [{"effective_at": (start + timedelta(minutes=5)).isoformat(), "interval_minutes": 60}]
        late = [{"effective_at": (start + timedelta(minutes=20)).isoformat(), "interval_minutes": 60}]
        end = start + timedelta(minutes=60)
        self.assertFalse(cadence.is_overdue(start, end, early))
        self.assertTrue(cadence.is_overdue(start, end, late))


if __name__ == "__main__":
    unittest.main()
