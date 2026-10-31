"""Persistent collection preferences and effective-time change history."""
from __future__ import annotations

import sqlite3

from . import db, settings

DEFAULT_INTERVAL_MINUTES = 10
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY, value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_interval_history (
    id INTEGER PRIMARY KEY,
    effective_at TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL,
    changed_by TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collection_interval_effective
ON collection_interval_history(effective_at, id);
"""


def initialize() -> None:
    """Create preference storage without changing prior values or history."""
    with db.connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO metadata(key,value) VALUES('collection_interval_minutes',?)",
            (str(DEFAULT_INTERVAL_MINUTES),),
        )
        conn.execute(
            "INSERT OR IGNORE INTO metadata(key,value) VALUES('collection_interval_updated_at','')"
        )


def _interval_value(value: str | None) -> int:
    try:
        interval = int(value) if value is not None else DEFAULT_INTERVAL_MINUTES
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_MINUTES
    if not MIN_INTERVAL_MINUTES <= interval <= MAX_INTERVAL_MINUTES:
        return DEFAULT_INTERVAL_MINUTES
    return interval


def get_config() -> dict:
    """Read current settings; old databases without preferences use 10 minutes."""
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT key,value FROM metadata WHERE key IN "
                "('collection_interval_minutes','collection_interval_updated_at')"
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        rows = []
    values = {row["key"]: row["value"] for row in rows}
    return {
        "interval_minutes": _interval_value(values.get("collection_interval_minutes")),
        "updated_at": values.get("collection_interval_updated_at") or None,
    }


def get_interval_minutes() -> int:
    return get_config()["interval_minutes"]


def set_interval(minutes: int, changed_by: str) -> dict:
    """Atomically change the preference and record when a new value took effect."""
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ValueError("采集间隔必须是整数分钟")
    if not MIN_INTERVAL_MINUTES <= minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(f"采集间隔需为 {MIN_INTERVAL_MINUTES}–{MAX_INTERVAL_MINUTES} 分钟")
    if not isinstance(changed_by, str):
        raise ValueError("操作人必须是字符串")
    initialize()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute(
            "SELECT value FROM metadata WHERE key='collection_interval_minutes'"
        ).fetchone()
        previous_time = conn.execute(
            "SELECT value FROM metadata WHERE key='collection_interval_updated_at'"
        ).fetchone()["value"]
        if _interval_value(old["value"]) == minutes:
            return {"interval_minutes": minutes, "updated_at": previous_time or None}
        effective_at = settings.stamp()
        conn.execute(
            "UPDATE metadata SET value=? WHERE key='collection_interval_minutes'", (str(minutes),)
        )
        conn.execute(
            "UPDATE metadata SET value=? WHERE key='collection_interval_updated_at'", (effective_at,)
        )
        conn.execute(
            "INSERT INTO collection_interval_history(effective_at,interval_minutes,changed_by) "
            "VALUES(?,?,?)", (effective_at, minutes, changed_by),
        )
        return {"interval_minutes": minutes, "updated_at": effective_at}


def get_history() -> list[dict]:
    """Return effective-time changes; the initial implicit default has no row."""
    try:
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT id,effective_at,interval_minutes,changed_by "
                "FROM collection_interval_history ORDER BY effective_at,id"
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        return []
    return [dict(row) for row in rows]
