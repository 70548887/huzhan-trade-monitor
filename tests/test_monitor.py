from __future__ import annotations

import csv
import io
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from bs4 import BeautifulSoup

from huzhan_monitor import auth, collector, db, reports, settings
from huzhan_monitor.web import app


def row(title="商品一", buyer="甲*", amount=15000, status="pending", link="https://www.huzhan.com/code/goods1.html"):
    item = {
        "title": title, "buyer": buyer, "amount_cents": amount,
        "status": status, "status_text": {"pending": "正在交易", "success": "交易成功", "unknown": "已取消"}[status],
        "product_url": link, "avatar_url": "https://qn_iu.huzhan.com/sign/a.jpg",
    }
    item["fingerprint"] = collector.fingerprint(item)
    return item


class MonitorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_db = settings.DATA_DIR, settings.DB_PATH
        settings.DATA_DIR = Path(self.temp.name)
        settings.DB_PATH = settings.DATA_DIR / "monitor.sqlite3"
        db.initialize()
        self.start = datetime(2026, 1, 1, 23, 50, tzinfo=settings.BEIJING)

    def tearDown(self):
        settings.DATA_DIR, settings.DB_PATH = self.old_data, self.old_db
        self.temp.cleanup()

    def collect(self, when, rows, cents=100000, count=10):
        at = settings.stamp(when)
        run_id = db.begin_run(at)
        return db.record_success(run_id, at, rows, {"total_cents": cents, "transaction_count": count}, b"home", b"response")

    def test_parse_no_link_money_duplicate_and_unknown(self):
        html = ('<li><img src="https://qn_iu.huzhan.com/sign/none.jpg?size=28">'
                '<a href="javascript:void(0)"><i>[已取消]</i><strong>甲*</strong>'
                '<em>2,599.00</em><span>自助商品</span></a></li>')
        rows = collector.parse_feed(html + html)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["amount_cents"], 259900)
        self.assertIsNone(rows[0]["product_url"])
        self.assertEqual(rows[0]["status"], "unknown")
        self.assertEqual(rows[0]["fingerprint"], rows[1]["fingerprint"])
        with self.assertRaises(collector.FeedError):
            collector.parse_feed("<li>invalid</li>")
        with self.assertRaises(collector.FeedError):
            collector.parse_feed("")

    def test_baseline_duplicate_repeat_and_success_after_midnight(self):
        pending = row()
        initial_success = row(title="旧记录", buyer="乙*", amount=20000, status="success")
        result = self.collect(self.start, [pending, pending, initial_success])
        self.assertEqual((result["baseline"], result["duplicates"]), (True, 1))
        self.collect(self.start + timedelta(minutes=5), [pending, initial_success])
        successful = dict(pending, status="success", status_text="交易成功")
        self.collect(self.start + timedelta(minutes=20), [successful, initial_success], 101000, 11)
        self.collect(self.start + timedelta(days=1), [successful, initial_success], 101000, 11)
        first_day = reports.daily_summary(date(2026, 1, 1))
        second_day = reports.daily_summary(date(2026, 1, 2))
        self.assertEqual(first_day["baseline_success"]["cents"], 20000)
        self.assertEqual(first_day["success"]["cents"], 0)
        self.assertEqual(second_day["success"], {"count": 1, "cents": 15000})
        with db.connect() as conn:
            item = conn.execute("SELECT * FROM items WHERE fingerprint=?", (pending["fingerprint"],)).fetchone()
            self.assertEqual(item["seen_runs"], 4)
            self.assertEqual(item["seen_rows"], 5)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM status_history WHERE item_id=?", (item["id"],)).fetchone()[0], 2)

    def test_identical_display_is_conservatively_merged(self):
        self.collect(self.start, [row(status="success")])
        self.collect(self.start + timedelta(minutes=10), [row(status="success"), row(status="success")])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT seen_rows FROM items").fetchone()[0], 3)

    def test_unknown_status_is_visible_on_observed_day_after_other_states(self):
        pending = row()
        self.collect(self.start, [pending])
        later = self.start + timedelta(minutes=20)
        self.collect(later, [dict(pending, status="unknown", status_text="已取消")])
        self.collect(later + timedelta(minutes=10), [dict(pending, status="success", status_text="交易成功")])
        entries = reports.detailed_rows(date(2026, 1, 2), "unknown")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["report_time"], settings.stamp(later))

    def test_failure_only_is_unavailable_and_interruption_is_recorded(self):
        at = settings.stamp(self.start)
        first = db.begin_run(at)
        second = db.begin_run(settings.stamp(self.start + timedelta(minutes=5)))
        db.record_failure(second, settings.stamp(self.start + timedelta(minutes=6)), "network")
        summary = reports.daily_summary(date(2026, 1, 1))
        self.assertFalse(summary["available"])
        self.assertIsNone(summary["success"]["cents"])
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT status FROM runs WHERE id=?", (first,)).fetchone()[0], "failed")
        history = reports.coverage(date(2026, 1, 1), now=self.start + timedelta(hours=1))
        self.assertEqual(len(history["failures"]), 2)
        # Monitoring only began at 23:50, so the earlier part of the day is
        # unobserved, not a fabricated 24-hour outage.
        self.assertFalse(history["gaps"])

    def test_report_export_and_product_totals(self):
        self.collect(self.start, [row(buyer="base", status="pending")])
        at = self.start + timedelta(minutes=20)
        self.collect(at, [row(title="演示", buyer="a", amount=12345, status="success"),
                          row(title="演示", buyer="b", amount=12345, status="success")])
        target = date(2026, 1, 2)
        summary = reports.daily_summary(target)
        products = reports.product_summary(target)
        with TestClient(app) as client:
            auth.set_admin("testadmin", "test-password-12345", reset=True)
            login_page = client.get("/login")
            csrf = BeautifulSoup(login_page.text, "html.parser").select_one('[name="csrf_token"]')["value"]
            client.post("/login", data={"username": "testadmin", "password": "test-password-12345", "csrf_token": csrf})
            response = client.get("/api/export.csv", params={"day": target.isoformat(), "status": "success"})
        records = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(summary["success"], {"count": 2, "cents": 24690})
        self.assertEqual(products[0]["cents"], 24690)
        self.assertEqual(sum(round(float(x["金额(元)"]) * 100) for x in records), 24690)
        self.assertEqual(len(records), 2)

    def test_index_uses_actual_boundary_snapshots_and_detects_rollback(self):
        self.collect(self.start, [row()], 100000, 10)
        self.collect(self.start + timedelta(minutes=15), [row()], 105000, 11)
        self.collect(self.start + timedelta(days=1, minutes=5), [row()], 130000, 13)
        self.collect(self.start + timedelta(days=1, minutes=15), [row()], 135000, 14)
        index = reports.platform_index(date(2026, 1, 2), now=self.start + timedelta(days=2))
        self.assertEqual(index["delta_cents"], 35000)
        self.assertTrue(index["start_boundary_ok"])
        self.assertTrue(index["end_boundary_ok"])
        self.collect(self.start + timedelta(days=1, minutes=7), [row()], 99000, 9)
        index = reports.platform_index(date(2026, 1, 2), now=self.start + timedelta(days=2))
        self.assertTrue(index["rollback"])
        self.assertIsNone(index["delta_cents"])

    def test_daily_backup_is_valid_and_readable(self):
        self.collect(self.start, [row()])
        db.backup_and_prune(self.start)
        backup = settings.DATA_DIR / "backups" / "huzhan-2026-01-01.sqlite3"
        self.assertTrue(backup.is_file())
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM items").fetchone()[0], 1)

    def test_trial_waits_24_hours_and_uses_real_run_history(self):
        entry = row()
        for i in range(144):
            self.collect(self.start + timedelta(minutes=10 * i), [entry])
        self.assertIsNone(db.evaluate_trial(self.start + timedelta(hours=23, minutes=59)))
        result = db.evaluate_trial(self.start + timedelta(hours=24))
        self.assertTrue(result["passed"])
        self.assertEqual(result["success_count"], 144)


if __name__ == "__main__":
    unittest.main()
