from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from huzhan_monitor import auth, db, reports, settings
from huzhan_monitor.web import app


class AuthenticationAndLogsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = settings.DATA_DIR, settings.DB_PATH, settings.COOKIE_SECURE
        self.old_auto = settings.AUTO_COLLECT
        settings.AUTO_COLLECT = False
        settings.DATA_DIR = Path(self.temp.name)
        settings.DB_PATH = settings.DATA_DIR / "monitor.sqlite3"
        settings.COOKIE_SECURE = False
        self.environment = patch.dict(os.environ, {"HUZHAN_ADMIN_PASSWORD": ""})
        self.environment.start()
        self.client_context = TestClient(app, follow_redirects=False)
        self.client = self.client_context.__enter__()
        self.password = "regression-only-password-82731"

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.environment.stop()
        settings.DATA_DIR, settings.DB_PATH, settings.COOKIE_SECURE = self.original
        settings.AUTO_COLLECT = self.old_auto
        self.temp.cleanup()

    def configure(self):
        auth.set_admin("admin", self.password)

    def login(self, password=None, origin=None):
        page = self.client.get("/login")
        csrf = BeautifulSoup(page.text, "html.parser").select_one('[name="csrf_token"]')["value"]
        return self.client.post("/login", data={"username": "admin", "password": password or self.password,
                                               "csrf_token": csrf, "next": "/logs"},
                                headers={"Origin": origin} if origin else None)

    def test_no_admin_and_no_cookie_cannot_read_private_data(self):
        self.assertFalse(auth.is_configured())
        self.assertIn("尚未设置管理员", self.client.get("/login").text)
        for path in ("/", "/logs"):
            self.assertEqual(self.client.get(path).status_code, 303)
        for path in ("/api/summary", "/api/transactions", "/api/products", "/api/trend", "/api/logs", "/api/export.csv"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, path)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.get("/health").text, "huzhan-monitor-ok")

    def test_login_unlocks_pages_and_api_with_http_only_cookie(self):
        self.configure()
        response = self.login()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/logs")
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        for path in ("/", "/logs", "/api/summary", "/api/logs", "/api/export.csv"):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_missing_csrf_and_cross_site_origin_are_rejected(self):
        self.configure()
        self.assertEqual(self.client.post("/login", data={"username": "admin", "password": self.password}).status_code, 403)
        self.assertEqual(self.login(origin="https://evil.example").status_code, 403)
        self.assertEqual(self.client.get("/api/summary").status_code, 401)

    def test_https_origin_is_accepted_behind_http_proxy(self):
        self.configure()
        settings.COOKIE_SECURE = True
        page = self.client.get("/login")
        csrf = BeautifulSoup(page.text, "html.parser").select_one('[name="csrf_token"]')["value"]
        # A TLS proxy forwards the browser's secure cookie to its HTTP backend.
        # An HTTP test client would otherwise omit this cookie itself.
        response = self.client.post("/login", data={"username": "admin", "password": self.password,
                                    "csrf_token": csrf},
                                    headers={"Origin": "https://testserver", "Cookie": "huzhan_login_csrf=" + csrf})
        self.assertEqual(response.status_code, 303)
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_logout_revokes_token_and_requires_session_csrf(self):
        self.configure()
        self.login()
        token = self.client.cookies.get("huzhan_session")
        csrf = BeautifulSoup(self.client.get("/").text, "html.parser").select_one('form[action="/logout"] input')["value"]
        self.assertEqual(self.client.post("/logout", data={"csrf_token": "invalid"}).status_code, 403)
        self.assertEqual(self.client.get("/api/summary").status_code, 200)
        self.assertEqual(self.client.post("/logout", data={"csrf_token": csrf}).status_code, 303)
        self.assertIsNone(auth.get_session(token))
        self.assertEqual(self.client.get("/api/summary").status_code, 401)

    def test_reset_and_expiry_invalidate_existing_sessions(self):
        self.configure()
        self.login()
        auth.set_admin("admin", "new-regression-password-91372", reset=True)
        self.assertEqual(self.client.get("/api/logs").status_code, 401)
        self.password = "new-regression-password-91372"
        self.login()
        with db.connect() as conn:
            conn.execute("UPDATE auth_sessions SET expires_at=?", (int(time.time()) - 1,))
        self.assertEqual(self.client.get("/api/summary").status_code, 401)

    def test_attempt_limit_persists_across_clients(self):
        self.configure()
        for _ in range(5):
            self.assertEqual(self.login(password="incorrect-password-123").status_code, 401)
        self.assertEqual(self.login().status_code, 429)
        self.assertEqual(auth.login("admin", self.password, "testclient")["status"], "throttled")

    def test_login_redirect_is_local_only(self):
        self.configure()
        page = self.client.get("/login?next=https://evil.example")
        fields = BeautifulSoup(page.text, "html.parser")
        response = self.client.post("/login", data={"username": "admin", "password": self.password,
                                    "csrf_token": fields.select_one('[name="csrf_token"]')["value"],
                                    "next": "https://evil.example"})
        self.assertEqual(response.headers["location"], "/")

    def test_empty_logs_do_not_report_midnight_as_an_outage(self):
        self.configure()
        self.login()
        report = self.client.get("/api/logs").json()
        self.assertEqual(report["total"], 0)
        self.assertTrue(any("尚未执行采集" in n["message"] for n in report["notices"]))
        self.assertEqual(reports.coverage(settings.now_beijing().date())["gaps"], [])

    def test_logs_paginate_and_filter_real_run_history(self):
        self.configure()
        self.login()
        start = datetime(2026, 1, 1, 12, tzinfo=settings.BEIJING)
        for i in range(57):
            at = settings.stamp(start + timedelta(seconds=20 * i))
            run_id = db.begin_run(at)
            db.record_failure(run_id, at, "HTTPStatusError: 403 Forbidden")
        result = self.client.get("/api/logs?day=2026-01-01&status=failed&page=3&page_size=20").json()
        self.assertEqual((result["total"], result["pages"], len(result["entries"])), (57, 3, 17))
        self.assertEqual(result["summary"]["failed"], 57)
        self.assertEqual(result["entries"][0]["error"], "HTTPStatusError: 403 Forbidden")
        empty = self.client.get("/api/logs?day=2026-01-01&status=success").json()
        self.assertEqual(empty["total"], 0)


class EmbeddedCollectionLifecycleTest(unittest.TestCase):
    def test_web_lifecycle_starts_and_stops_only_when_enabled(self):
        for enabled in (True, False):
            with self.subTest(auto_collect=enabled), tempfile.TemporaryDirectory() as folder:
                data = Path(folder)
                with patch.multiple(settings, DATA_DIR=data, DB_PATH=data / "monitor.sqlite3", AUTO_COLLECT=enabled), \
                     patch.dict(os.environ, {"HUZHAN_ADMIN_PASSWORD": ""}), \
                     patch("huzhan_monitor.scheduler.Scheduler") as factory:
                    scheduler = factory.return_value
                    scheduler.start.return_value = scheduler
                    scheduler.stop.return_value = True
                    with TestClient(app) as client:
                        response = client.get("/health")
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers["x-huzhan-auto-collect"], "1" if enabled else "0")
                        if enabled:
                            factory.assert_called_once_with()
                            scheduler.start.assert_called_once_with()
                            scheduler.stop.assert_not_called()
                        else:
                            factory.assert_not_called()
                    if enabled:
                        scheduler.stop.assert_called_once_with(timeout=35)


if __name__ == "__main__":
    unittest.main()
