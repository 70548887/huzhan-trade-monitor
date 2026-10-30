"""Local-only dashboard and read-only reporting endpoints."""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, reports, settings

HERE = Path(__file__).resolve().parent
app = FastAPI(title="互站交易监控", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


@app.on_event("startup")
def init_database() -> None:
    db.initialize()


def day_arg(value: str | None) -> date:
    if not value:
        return settings.now_beijing().date()
    try:
        return reports.parse_day(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD") from None


def state_arg(value: str) -> str:
    if value != "all" and value not in reports.STATUS_CHOICES:
        raise HTTPException(status_code=400, detail="无效的状态")
    return value


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/summary")
def summary(day: str | None = None):
    selected = day_arg(day)
    trial = db.evaluate_trial()
    with db.connect() as conn:
        trial_started = conn.execute("SELECT value FROM metadata WHERE key='trial_started'").fetchone()
        maintenance = conn.execute("SELECT value FROM metadata WHERE key='maintenance_error'").fetchone()
    return {
        **reports.daily_summary(selected),
        **reports.pending_summary(selected),
        "coverage": reports.coverage(selected),
        "platform": reports.platform_index(selected),
        "trial": trial,
        "trial_started_at": trial_started["value"] if trial_started else None,
        "maintenance_error": maintenance["value"] if maintenance else None,
    }


@app.get("/api/transactions")
def transactions(day: str | None = None, status: str = "all", q: str = Query("", max_length=200)):
    return {"items": reports.detailed_rows(day_arg(day), state_arg(status), q)}


@app.get("/api/products")
def products(day: str | None = None):
    return {"items": reports.product_summary(day_arg(day))}


@app.get("/api/trend")
def trends(day: str | None = None, days: int = Query(14, ge=1, le=90)):
    return {"items": reports.trend(day_arg(day), days)}


@app.get("/api/export.csv")
def export(day: str | None = None, status: str = "all", q: str = Query("", max_length=200)):
    selected = day_arg(day)
    rows = reports.detailed_rows(selected, state_arg(status), q)
    content = io.StringIO(newline="")
    writer = csv.writer(content)
    writer.writerow(["统计日期", "统计类别", "商品", "金额(元)", "买家", "状态", "统计观察时间",
                     "首次观察", "最近观察", "商品链接", "状态异常"])
    names = {"success": "新增成功", "pending": "新增进行中", "baseline": "启动时存量", "unknown": "未知状态",
             "observed_pending": "当日观察到交易中", "snapshot_pending": "最后采集交易中"}
    for row in rows:
        # Spreadsheet applications may execute formula-like cells on opening CSV.
        def safe(value):
            text = str(value or "")
            return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text
        writer.writerow([
            selected.isoformat(), names[row["category"]], safe(row["title"]),
            f"{row['amount_cents'] // 100}.{row['amount_cents'] % 100:02d}",
            safe(row["buyer"]), safe(row["current_status_text"]), row["report_time"],
            row["first_seen_at"], row["last_seen_at"], safe(row["product_url"]), "是" if row["status_conflict"] else "",
        ])
    return Response(
        content=("\ufeff" + content.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="huzhan-{selected.isoformat()}.csv"'},
    )


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "huzhan-monitor-ok"
