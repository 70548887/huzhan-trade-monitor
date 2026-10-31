"""Assess collection coverage against the frequency in effect at the time."""
from __future__ import annotations

from datetime import datetime, timedelta

from .preferences import DEFAULT_INTERVAL_MINUTES

GRACE_SECONDS = 300


def next_due(anchor: datetime, until: datetime, history: list[dict]) -> tuple[datetime, int]:
    interval = DEFAULT_INTERVAL_MINUTES
    for change in history:
        if datetime.fromisoformat(change["effective_at"]) <= anchor:
            interval = change["interval_minutes"]
    due = anchor + timedelta(minutes=interval)
    for change in history:
        changed_at = datetime.fromisoformat(change["effective_at"])
        if changed_at <= anchor:
            continue
        if changed_at > until:
            break
        # Once a run was already overdue, a later extension cannot erase it.
        if changed_at > due + timedelta(seconds=GRACE_SECONDS):
            break
        interval = change["interval_minutes"]
        due = max(changed_at, anchor + timedelta(minutes=interval))
    return due, interval


def is_overdue(anchor: datetime, until: datetime, history: list[dict]) -> bool:
    due, _ = next_due(anchor, until, history)
    return until > due + timedelta(seconds=GRACE_SECONDS)


def expected_collections(start: datetime, end: datetime, history: list[dict]) -> int:
    count = 1
    cursor = start
    while cursor < end:
        due, _ = next_due(cursor, end, history)
        if due >= end:
            break
        count += 1
        cursor = due
    return count
