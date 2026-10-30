"""SQLite storage, transaction history and maintenance."""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'success', 'failed')),
    error TEXT,
    row_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE TABLE IF NOT EXISTS snapshots (
    run_id INTEGER PRIMARY KEY REFERENCES runs(id),
    created_at TEXT NOT NULL,
    homepage_gzip BLOB,
    response_gzip BLOB
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    product_url TEXT,
    buyer TEXT NOT NULL,
    avatar_url TEXT,
    amount_cents INTEGER NOT NULL,
    current_status TEXT NOT NULL,
    current_status_text TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    first_pending_at TEXT,
    first_success_at TEXT,
    baseline_entry INTEGER NOT NULL DEFAULT 0,
    baseline_success INTEGER NOT NULL DEFAULT 0,
    seen_runs INTEGER NOT NULL DEFAULT 0,
    seen_rows INTEGER NOT NULL DEFAULT 0,
    status_conflict INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_success ON items(first_success_at, baseline_success);
CREATE INDEX IF NOT EXISTS idx_items_pending ON items(first_pending_at, baseline_entry);
CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    status_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_item ON observations(item_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_observations_status_time ON observations(status, observed_at, item_id);
CREATE INDEX IF NOT EXISTS idx_observations_run_status ON observations(run_id, status, item_id);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    item_id INTEGER NOT NULL REFERENCES items(id),
    observed_at TEXT NOT NULL,
    status TEXT NOT NULL,
    status_text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS platform_snapshots (
    run_id INTEGER PRIMARY KEY REFERENCES runs(id),
    observed_at TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    transaction_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_time ON platform_snapshots(observed_at);
"""


class ManagedConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DB_PATH, timeout=20, factory=ManagedConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def initialize() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def begin_run(started_at: str) -> int:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET status='failed',finished_at=?,error=? WHERE status='running'",
            (started_at, "上次采集意外中断，恢复后已记录此缺口"),
        )
        cur = conn.execute(
            "INSERT INTO runs(started_at, status) VALUES (?, 'running')", (started_at,)
        )
        return int(cur.lastrowid)


def _packed(data: bytes | None) -> bytes | None:
    return gzip.compress(data, compresslevel=6) if data is not None else None


def record_failure(
    run_id: int, finished_at: str, error: str,
    homepage: bytes | None = None, response: bytes | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE runs SET finished_at=?, status='failed', error=? WHERE id=?",
            (finished_at, error[:1000], run_id),
        )
        if homepage is not None or response is not None:
            conn.execute(
                "INSERT INTO snapshots(run_id,created_at,homepage_gzip,response_gzip) "
                "VALUES(?,?,?,?)",
                (run_id, finished_at, _packed(homepage), _packed(response)),
            )


def record_success(
    run_id: int, observed_at: str, rows: list[dict], platform: dict,
    homepage: bytes, response: bytes,
) -> dict:
    """Insert one complete snapshot atomically; first complete snapshot is the baseline."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["fingerprint"], []).append(row)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        baseline = conn.execute(
            "SELECT 1 FROM metadata WHERE key='baseline_completed'"
        ).fetchone() is None
        for fingerprint, group in groups.items():
            first = group[0]
            old = conn.execute(
                "SELECT * FROM items WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            # Conflicting statuses are retained as observations and flagged; success
            # takes precedence for the snapshot because it has been publicly observed.
            states = {(r["status"], r["status_text"]) for r in group}
            selected = next((r for r in group if r["status"] == "success"), first)
            has_pending = any(r["status"] == "pending" for r in group)
            has_success = any(r["status"] == "success" for r in group)
            conflict = len(states) > 1 or (
                old is not None and old["first_success_at"] is not None
                and selected["status"] == "pending"
            )
            if old is None:
                cur = conn.execute(
                    """INSERT INTO items(
                        fingerprint,title,product_url,buyer,avatar_url,amount_cents,
                        current_status,current_status_text,first_seen_at,last_seen_at,
                        first_pending_at,first_success_at,baseline_entry,baseline_success,
                        seen_runs,seen_rows,status_conflict
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        fingerprint, first["title"], first["product_url"], first["buyer"],
                        first["avatar_url"], first["amount_cents"], selected["status"],
                        selected["status_text"], observed_at, observed_at,
                        observed_at if has_pending else None,
                        observed_at if has_success else None,
                        int(baseline), int(baseline and has_success),
                        1, len(group), int(conflict),
                    ),
                )
                item_id = int(cur.lastrowid)
                changed = True
            else:
                item_id = int(old["id"])
                changed = (old["current_status"], old["current_status_text"]) != (
                    selected["status"], selected["status_text"]
                )
                first_success = old["first_success_at"] or (
                    observed_at if has_success else None
                )
                first_pending = old["first_pending_at"] or (
                    observed_at if has_pending else None
                )
                conn.execute(
                    """UPDATE items SET title=?,product_url=?,last_seen_at=?,
                       current_status=?,current_status_text=?,first_pending_at=?,
                       first_success_at=?,seen_runs=seen_runs+1,seen_rows=seen_rows+?,
                       status_conflict=MAX(status_conflict,?) WHERE id=?""",
                    (
                        first["title"], first["product_url"], observed_at,
                        selected["status"], selected["status_text"], first_pending,
                        first_success, len(group), int(conflict), item_id,
                    ),
                )
            if changed:
                conn.execute(
                    "INSERT INTO status_history(run_id,item_id,observed_at,status,status_text) "
                    "VALUES(?,?,?,?,?)",
                    (run_id, item_id, observed_at, selected["status"], selected["status_text"]),
                )
            for row in group:
                conn.execute(
                    "INSERT INTO observations(run_id,item_id,observed_at,status,status_text) "
                    "VALUES(?,?,?,?,?)",
                    (run_id, item_id, observed_at, row["status"], row["status_text"]),
                )

        conn.execute(
            "INSERT INTO platform_snapshots(run_id,observed_at,total_cents,transaction_count) "
            "VALUES(?,?,?,?)",
            (run_id, observed_at, platform["total_cents"], platform["transaction_count"]),
        )
        conn.execute(
            "INSERT INTO snapshots(run_id,created_at,homepage_gzip,response_gzip) VALUES(?,?,?,?)",
            (run_id, observed_at, _packed(homepage), _packed(response)),
        )
        duplicate_count = len(rows) - len(groups)
        conn.execute(
            "UPDATE runs SET finished_at=?,status='success',row_count=?,duplicate_count=? WHERE id=?",
            (observed_at, len(rows), duplicate_count, run_id),
        )
        if baseline:
            conn.execute("INSERT INTO metadata(key,value) VALUES('baseline_completed',?)", (observed_at,))
            conn.execute("INSERT INTO metadata(key,value) VALUES('trial_started',?)", (observed_at,))
    return {"baseline": baseline, "rows": len(rows), "unique": len(groups),
            "duplicates": duplicate_count}


def backup_and_prune(at: datetime | None = None) -> None:
    """Create a daily SQLite-consistent backup and prune only managed files."""
    at = at or settings.now_beijing()
    backup_dir = settings.DATA_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"huzhan-{at.date().isoformat()}.sqlite3"
    if not target.exists():
        partial = target.with_suffix(".sqlite3.part")
        source = connect()
        destination = sqlite3.connect(partial)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        os.replace(partial, target)
    backups = sorted(backup_dir.glob("huzhan-????-??-??.sqlite3"), reverse=True)
    for path in backups[settings.BACKUP_RETENTION_COUNT:]:
        path.unlink()
    cutoff = settings.stamp(at - timedelta(days=settings.RAW_RETENTION_DAYS))
    with connect() as conn:
        conn.execute("DELETE FROM snapshots WHERE created_at < ?", (cutoff,))


def maintenance_status(error: str | None) -> None:
    with connect() as conn:
        if error is None:
            conn.execute("DELETE FROM metadata WHERE key='maintenance_error'")
        else:
            conn.execute(
                "INSERT INTO metadata(key,value) VALUES('maintenance_error',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (error[:1000],)
            )


def evaluate_trial(at: datetime | None = None) -> dict | None:
    """After 24 hours, persist a real coverage report; never infer success early."""
    at = at or settings.now_beijing()
    with connect() as conn:
        existing = conn.execute("SELECT value FROM metadata WHERE key='trial_report'").fetchone()
        if existing:
            return json.loads(existing["value"])
        start_row = conn.execute("SELECT value FROM metadata WHERE key='trial_started'").fetchone()
        if not start_row:
            return None
        start = datetime.fromisoformat(start_row["value"])
        end = start + timedelta(hours=24)
        if at < end:
            return None
        records = conn.execute(
            "SELECT finished_at,status FROM runs WHERE finished_at >= ? AND finished_at < ? "
            "ORDER BY finished_at", (settings.stamp(start), settings.stamp(end))
        ).fetchall()
        times = [datetime.fromisoformat(r["finished_at"]) for r in records if r["status"] == "success"]
        edges = [start, *times, end]
        max_gap = max((b - a).total_seconds() for a, b in zip(edges, edges[1:]))
        failed = sum(r["status"] != "success" for r in records)
        report = {
            "started_at": settings.stamp(start), "ended_at": settings.stamp(end),
            "checked_at": settings.stamp(at), "success_count": len(times),
            "failed_count": failed, "max_gap_minutes": round(max_gap / 60, 1),
            "passed": len(times) >= 144 and failed == 0 and max_gap <= 15 * 60,
        }
        conn.execute("INSERT INTO metadata(key,value) VALUES('trial_report',?)", (json.dumps(report),))
        return report
