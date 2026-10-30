"""Read-only daily reports, platform index comparisons and coverage."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from . import db, settings

STATUS_CHOICES = {"success", "pending", "unknown", "baseline", "observed_pending", "snapshot_pending"}


def day_bounds(day: date) -> tuple[str, str]:
    start = datetime.combine(day, time.min, tzinfo=settings.BEIJING)
    return settings.stamp(start), settings.stamp(start + timedelta(days=1))


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def detailed_rows(day: date, status: str = "all", search: str = "") -> list[dict]:
    start, end = day_bounds(day)
    criteria = {
        "success": ("first_success_at >= ? AND first_success_at < ? AND baseline_success=0", (start, end), "first_success_at"),
        "pending": ("first_pending_at >= ? AND first_pending_at < ? AND baseline_entry=0", (start, end), "first_pending_at"),
        "baseline": ("first_seen_at >= ? AND first_seen_at < ? AND baseline_entry=1", (start, end), "first_seen_at"),
    }
    chosen = [status] if status in STATUS_CHOICES else ["success", "pending", "baseline", "unknown"]
    query_parts, params = [], []
    for key in chosen:
        if key in ("observed_pending", "snapshot_pending"):
            query = (
                "SELECT i.id,i.title,i.product_url,i.buyer,i.amount_cents,"
                "'pending' AS current_status,'正在交易' AS current_status_text,"
                "i.first_seen_at,i.last_seen_at,i.first_success_at,i.seen_rows,i.status_conflict,"
                f"'{key}' AS category,MIN(o.observed_at) AS report_time "
                "FROM items i JOIN observations o ON o.item_id=i.id "
                "JOIN runs r ON r.id=o.run_id "
                "WHERE o.status='pending' AND r.status='success' "
            )
            if key == "observed_pending":
                query += "AND o.observed_at>=? AND o.observed_at<? "
            else:
                query += (
                    "AND r.id=(SELECT id FROM runs WHERE status='success' "
                    "AND finished_at>=? AND finished_at<? ORDER BY finished_at DESC,id DESC LIMIT 1) "
                )
            query_parts.append(query + "GROUP BY i.id")
            params.extend((start, end))
            continue
        if key == "unknown":
            query_parts.append(
                "SELECT id,title,product_url,buyer,amount_cents,current_status,current_status_text,"
                "first_seen_at,last_seen_at,first_success_at,seen_rows,status_conflict,"
                "'unknown' AS category,(SELECT MIN(o.observed_at) FROM observations o "
                "WHERE o.item_id=items.id AND o.status='unknown' AND o.observed_at>=? "
                "AND o.observed_at<?) AS report_time FROM items WHERE EXISTS "
                "(SELECT 1 FROM observations o WHERE o.item_id=items.id AND o.status='unknown' "
                "AND o.observed_at>=? AND o.observed_at<?)"
            )
            params.extend((start, end, start, end))
            continue
        where, values, date_field = criteria[key]
        query_parts.append(
            f"SELECT id,title,product_url,buyer,amount_cents,current_status,current_status_text,"
            f"first_seen_at,last_seen_at,first_success_at,seen_rows,status_conflict,"
            f"'{key}' AS category,{date_field} AS report_time FROM items WHERE {where}"
        )
        params.extend(values)
    sql = "SELECT * FROM (" + " UNION ALL ".join(query_parts) + ")"
    if search.strip():
        # LIKE metacharacters are escaped so searches are literal.
        query = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += " WHERE title LIKE ? ESCAPE '\\'"
        params.append("%" + query + "%")
    sql += " ORDER BY report_time DESC,id DESC"
    with db.connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def pending_summary(day: date) -> dict:
    """Distinct pending observations and the last successful snapshot for a day."""
    start, end = day_bounds(day)
    with db.connect() as conn:
        snapshot = conn.execute(
            "SELECT id,finished_at FROM runs WHERE status='success' "
            "AND finished_at>=? AND finished_at<? ORDER BY finished_at DESC,id DESC LIMIT 1",
            (start, end),
        ).fetchone()
        if snapshot is None:
            return {
                "observed_pending": {"count": None, "cents": None},
                "snapshot_pending": {"count": None, "cents": None, "observed_at": None},
            }
        observed = conn.execute(
            """SELECT COUNT(*) AS count,COALESCE(SUM(i.amount_cents),0) AS cents
               FROM items i WHERE i.id IN (
                   SELECT DISTINCT o.item_id FROM observations o
                   JOIN runs r ON r.id=o.run_id
                   WHERE o.status='pending' AND r.status='success'
                   AND o.observed_at>=? AND o.observed_at<?
               )""", (start, end),
        ).fetchone()
        latest = conn.execute(
            """SELECT COUNT(*) AS count,COALESCE(SUM(i.amount_cents),0) AS cents
               FROM items i WHERE i.id IN (
                   SELECT DISTINCT item_id FROM observations
                   WHERE run_id=? AND status='pending'
               )""", (snapshot["id"],),
        ).fetchone()
        return {
            "observed_pending": dict(observed),
            "snapshot_pending": {**dict(latest), "observed_at": snapshot["finished_at"]},
        }


def daily_summary(day: date) -> dict:
    start, end = day_bounds(day)
    with db.connect() as conn:
        success = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(amount_cents),0) AS cents FROM items "
            "WHERE first_success_at >= ? AND first_success_at < ? AND baseline_success=0",
            (start, end),
        ).fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(amount_cents),0) AS cents FROM items "
            "WHERE first_pending_at >= ? AND first_pending_at < ? AND baseline_entry=0",
            (start, end),
        ).fetchone()
        baseline = conn.execute(
            "SELECT COUNT(*) AS count,COALESCE(SUM(amount_cents),0) AS cents FROM items "
            "WHERE first_seen_at >= ? AND first_seen_at < ? AND baseline_success=1",
            (start, end),
        ).fetchone()
        runs = conn.execute(
            "SELECT COUNT(*) AS total,SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,"
            "SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed,"
            "COALESCE(SUM(duplicate_count),0) AS duplicates FROM runs "
            "WHERE started_at >= ? AND started_at < ?", (start, end),
        ).fetchone()
        latest = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        last_success = conn.execute(
            "SELECT finished_at FROM runs WHERE status='success' ORDER BY finished_at DESC LIMIT 1"
        ).fetchone()
        baseline_at = conn.execute("SELECT value FROM metadata WHERE key='baseline_completed'").fetchone()
    available = bool(runs["success"])
    unavailable = {"count": None, "cents": None}
    return {
        "date": day.isoformat(), "available": available,
        "success": dict(success) if available else dict(unavailable),
        "pending": dict(pending) if available else dict(unavailable),
        "baseline_success": dict(baseline) if available else dict(unavailable), "runs": dict(runs),
        "latest_run": dict(latest) if latest else None,
        "last_success_at": last_success["finished_at"] if last_success else None,
        "baseline_at": baseline_at["value"] if baseline_at else None,
    }


def product_summary(day: date) -> list[dict]:
    start, end = day_bounds(day)
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT title,product_url,COUNT(*) AS count,SUM(amount_cents) AS cents
            FROM items WHERE first_success_at >= ? AND first_success_at < ? AND baseline_success=0
            GROUP BY title,product_url ORDER BY cents DESC,count DESC,title""", (start, end)
        ).fetchall()
        return [dict(row) for row in rows]


def platform_index(day: date, now: datetime | None = None) -> dict:
    """Compare bracketing midnight observations, never label it an exact daily total."""
    now = now or settings.now_beijing()
    start = datetime.combine(day, time.min, tzinfo=settings.BEIJING)
    end = start + timedelta(days=1)
    with db.connect() as conn:
        before = conn.execute(
            "SELECT * FROM platform_snapshots WHERE observed_at <= ? "
            "ORDER BY observed_at DESC LIMIT 1", (settings.stamp(start),)
        ).fetchone()
        target = min(end, now)
        if now >= end:
            after = conn.execute(
                "SELECT * FROM platform_snapshots WHERE observed_at >= ? "
                "ORDER BY observed_at LIMIT 1", (settings.stamp(end),)
            ).fetchone()
        else:
            after = conn.execute(
                "SELECT * FROM platform_snapshots WHERE observed_at >= ? AND observed_at <= ? "
                "ORDER BY observed_at DESC LIMIT 1",
                (settings.stamp(start), settings.stamp(target)),
            ).fetchone()
        first = conn.execute(
            "SELECT * FROM platform_snapshots WHERE observed_at >= ? AND observed_at < ? "
            "ORDER BY observed_at LIMIT 1", (settings.stamp(start), settings.stamp(end))
        ).fetchone()
        last = conn.execute(
            "SELECT * FROM platform_snapshots WHERE observed_at >= ? AND observed_at < ? "
            "ORDER BY observed_at DESC LIMIT 1", (settings.stamp(start), settings.stamp(end))
        ).fetchone()
        all_day = conn.execute(
            "SELECT observed_at,total_cents,transaction_count FROM platform_snapshots "
            "WHERE observed_at >= ? AND observed_at < ? ORDER BY observed_at",
            (settings.stamp(start), settings.stamp(end)),
        ).fetchall()
    # If a boundary sample is absent, still show any within-day interval, flagged.
    left = before or first
    right = after or last
    points = ([before] if before else []) + list(all_day) + ([after] if after else [])
    rollback = any(
        b["total_cents"] < a["total_cents"] or b["transaction_count"] < a["transaction_count"]
        for a, b in zip(points, points[1:])
    )
    boundary_minutes = 15
    start_ok = bool(before and start - datetime.fromisoformat(before["observed_at"]) <= timedelta(minutes=boundary_minutes))
    if now >= end:
        end_ok = bool(after and datetime.fromisoformat(after["observed_at"]) - end <= timedelta(minutes=boundary_minutes))
    else:
        end_ok = bool(after and now - datetime.fromisoformat(after["observed_at"]) <= timedelta(minutes=boundary_minutes))
    valid_delta = left and right and left["observed_at"] != right["observed_at"] and not rollback
    return {
        "start_at": left["observed_at"] if left else None,
        "end_at": right["observed_at"] if right else None,
        "start_cents": left["total_cents"] if left else None,
        "end_cents": right["total_cents"] if right else None,
        "delta_cents": right["total_cents"] - left["total_cents"] if valid_delta else None,
        "delta_count": right["transaction_count"] - left["transaction_count"] if valid_delta else None,
        "start_boundary_ok": start_ok, "end_boundary_ok": end_ok,
        "rollback": rollback, "partial": now < end,
    }


def coverage(day: date, now: datetime | None = None) -> dict:
    now = now or settings.now_beijing()
    start = datetime.combine(day, time.min, tzinfo=settings.BEIJING)
    end = min(start + timedelta(days=1), now)
    if end <= start:
        return {"gaps": [], "failures": [], "stale": False}
    with db.connect() as conn:
        baseline_row = conn.execute(
            "SELECT value FROM metadata WHERE key='baseline_completed'"
        ).fetchone()
        first_run = conn.execute("SELECT MIN(started_at) AS started_at FROM runs").fetchone()
        success = conn.execute(
            "SELECT finished_at FROM runs WHERE status='success' AND finished_at >= ? "
            "AND finished_at <= ? ORDER BY finished_at", (settings.stamp(start), settings.stamp(end))
        ).fetchall()
        prior = conn.execute(
            "SELECT finished_at FROM runs WHERE status='success' AND finished_at < ? "
            "ORDER BY finished_at DESC LIMIT 1", (settings.stamp(start),)
        ).fetchone()
        failures = conn.execute(
            "SELECT started_at,error FROM runs WHERE status='failed' AND started_at >= ? "
            "AND started_at < ? ORDER BY started_at DESC", (settings.stamp(start), settings.stamp(end))
        ).fetchall()
    started_at = first_run["started_at"] or (baseline_row["value"] if baseline_row else None)
    if started_at is None or datetime.fromisoformat(started_at) >= end:
        return {"gaps": [], "failures": [dict(r) for r in failures], "stale": False}
    times = [datetime.fromisoformat(r["finished_at"]) for r in success]
    if prior:
        start_anchor = datetime.fromisoformat(prior["finished_at"])
    else:
        start_anchor = max(start, datetime.fromisoformat(started_at))
    edges = [start_anchor, *times, end]
    gaps = [
        {"start": settings.stamp(max(a, start)), "end": settings.stamp(b),
         "minutes": round((b - max(a, start)).total_seconds()/60, 1)}
        for a, b in zip(edges, edges[1:]) if b - a > timedelta(minutes=15)
    ]
    return {"gaps": gaps, "failures": [dict(r) for r in failures],
            "stale": bool(day == now.date() and gaps and gaps[-1]["end"] == settings.stamp(end))}


def run_log_report(day: date, status: str = "all", page: int = 1, page_size: int = 50) -> dict:
    start, end = day_bounds(day)
    where = "started_at>=? AND started_at<?"
    params: list = [start, end]
    if status != "all":
        where += " AND status=?"
        params.append(status)
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM runs WHERE " + where, params).fetchone()[0]
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        rows = conn.execute(
            "SELECT id,started_at,finished_at,status,error,row_count,duplicate_count FROM runs WHERE "
            + where + " ORDER BY started_at DESC,id DESC LIMIT ? OFFSET ?",
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
        counts = conn.execute(
            "SELECT status,COUNT(*) AS count,COALESCE(SUM(duplicate_count),0) AS duplicates "
            "FROM runs WHERE started_at>=? AND started_at<? GROUP BY status", (start, end),
        ).fetchall()
        meta = dict(conn.execute(
            "SELECT key,value FROM metadata WHERE key IN ('baseline_completed','trial_started','maintenance_error')"
        ).fetchall())
        first_run = conn.execute("SELECT MIN(started_at) AS started_at FROM runs").fetchone()[0]
    entries = []
    for record in rows:
        entry = dict(record)
        entry["duration_seconds"] = (
            max(0, round((datetime.fromisoformat(entry["finished_at"]) - datetime.fromisoformat(entry["started_at"])).total_seconds(), 1))
            if entry["finished_at"] else None
        )
        entries.append(entry)
    counts_summary = {"total": 0, "success": 0, "failed": 0, "running": 0, "duplicates": 0}
    for item in counts:
        counts_summary[item["status"]] = item["count"]
        counts_summary["total"] += item["count"]
        counts_summary["duplicates"] += item["duplicates"]
    daily = daily_summary(day)
    covered = coverage(day)
    index = platform_index(day)
    notices = []
    if first_run is None:
        message = ("尚未执行采集。内置自动采集已启用，正在等待首次执行；若持续无记录，请检查服务日志和目录权限。"
                   if settings.AUTO_COLLECT else "尚未执行采集。内置自动采集已关闭，请配置外部采集任务。")
        notices.append({"level": "info", "message": message})
    elif not daily["available"]:
        notices.append({"level": "warning" if counts_summary["total"] else "info",
                        "message": "所选日期没有成功采集记录，无法判断当天的交易金额。"})
    for gap in covered["gaps"]:
        notices.append({"level": "warning", "message": f"采集间隔缺口：{gap['start']} 至 {gap['end']}（该日期内 {gap['minutes']} 分钟）。"})
    if counts_summary["failed"]:
        notices.append({"level": "error", "message": f"所选日期有 {counts_summary['failed']} 次采集失败，可筛选失败记录查看具体原因。"})
    if index["start_at"] and (not index["start_boundary_ok"] or not index["end_boundary_ok"]):
        notices.append({"level": "warning", "message": "指数缺少靠近日期边界的快照，实际观测区间不等于完整自然日。"})
    if index["rollback"]:
        notices.append({"level": "error", "message": "平台累计指数出现回退，已停止计算该区间增量。"})
    if meta.get("maintenance_error"):
        notices.append({"level": "error", "message": "当前备份或试运行评估异常：" + meta["maintenance_error"]})
    return {
        "date": day.isoformat(), "entries": entries, "page": page, "page_size": page_size,
        "auto_collect": settings.AUTO_COLLECT,
        "total": total, "pages": pages, "summary": counts_summary, "notices": notices,
        "last_success_at": daily["last_success_at"], "baseline_at": meta.get("baseline_completed"),
        "trial": db.evaluate_trial(), "trial_started_at": meta.get("trial_started"),
        "index_window": {"start_at": index["start_at"] if index["end_at"] else None,
                         "end_at": index["end_at"] if index["start_at"] else None},
    }


def trend(end_day: date, days: int = 14) -> list[dict]:
    result = []
    for offset in reversed(range(days)):
        day = end_day - timedelta(days=offset)
        summary = daily_summary(day)
        index = platform_index(day)
        result.append({
            "date": day.isoformat(), "observed_cents": summary["success"]["cents"],
            "observed_count": summary["success"]["count"],
            "index_delta_cents": index["delta_cents"],
            "index_start_at": index["start_at"], "index_end_at": index["end_at"],
            "index_boundary_ok": index["start_boundary_ok"] and index["end_boundary_ok"],
            "index_rollback": index["rollback"],
        })
    return result
