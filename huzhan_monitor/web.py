"""Local-only dashboard and read-only reporting endpoints."""
from __future__ import annotations

import csv
import io
import hmac
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from . import auth, db, reports, settings

HERE = Path(__file__).resolve().parent
app = FastAPI(title="互站交易监控", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
SESSION_COOKIE = "huzhan_session"
LOGIN_CSRF_COOKIE = "huzhan_login_csrf"
SESSION_SECONDS = 12 * 60 * 60


@app.on_event("startup")
def init_database() -> None:
    db.initialize()
    auth.initialize()
    auth.bootstrap_from_environment()


@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    path = request.url.path
    public = path in ("/login", "/health") or path.startswith("/static/")
    request.state.user = None
    token = request.cookies.get(SESSION_COOKIE, "")
    if token and not path.startswith("/static/") and path != "/health":
        request.state.user = await run_in_threadpool(auth.get_session, token)
    if not public and request.state.user is None:
        if path.startswith("/api/"):
            response = JSONResponse({"detail": "请先登录"}, status_code=401)
        else:
            destination = "/logs" if path == "/logs" else "/"
            response = RedirectResponse("/login?next=" + destination, status_code=303)
    else:
        response = await call_next(request)
    if not path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def cookie_secure(request: Request) -> bool:
    return settings.COOKIE_SECURE or request.url.scheme == "https"


def allowed_destination(value: str) -> str:
    return value if value in ("/", "/logs") else "/"


def login_page(request: Request, *, error: str | None = None, status: int = 200,
               username: str = "", destination: str = "/"):
    csrf = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request, name="login.html",
        context={"configured": auth.is_configured(), "error": error, "csrf_token": csrf,
                 "next_path": allowed_destination(destination), "username": username},
        status_code=status,
    )
    response.set_cookie(LOGIN_CSRF_COOKIE, csrf, max_age=1800, httponly=True,
                        secure=cookie_secure(request), samesite="lax")
    return response


async def bounded_form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/x-www-form-urlencoded":
        raise HTTPException(status_code=400, detail="无效的表单格式")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 16384:
            raise HTTPException(status_code=413, detail="表单过长")
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True, max_num_fields=8)
    except (UnicodeDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="无效的表单内容") from None
    if any(len(values) != 1 for values in parsed.values()):
        raise HTTPException(status_code=400, detail="重复的表单字段")
    return {key: values[0] for key, values in parsed.items()}


def valid_csrf(request: Request, supplied: str, expected: str) -> bool:
    origin = request.headers.get("origin")
    if origin:
        try:
            source = urlsplit(origin)
            # The proxy may terminate HTTPS and forward plain HTTP internally.
            # Validate the public Host and the CSRF token without trusting a
            # client-supplied X-Forwarded-Proto header.
            if source.scheme not in ("http", "https") or source.netloc.lower() != request.url.netloc.lower():
                return False
        except ValueError:
            return False
    return bool(supplied and expected and len(supplied) <= 128 and len(expected) <= 128
                and hmac.compare_digest(supplied.encode(), expected.encode()))


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    destination = allowed_destination(request.query_params.get("next", "/"))
    if request.state.user:
        return RedirectResponse(destination, status_code=303)
    return login_page(request, destination=destination)


@app.post("/login", response_class=HTMLResponse)
async def login_post(request: Request):
    form = await bounded_form(request)
    destination = allowed_destination(form.get("next", "/"))
    if not valid_csrf(request, form.get("csrf_token", ""), request.cookies.get(LOGIN_CSRF_COOKIE, "")):
        return await run_in_threadpool(login_page, request, error="登录页面已过期，请重新提交。", status=403, destination=destination)
    username = form.get("username", "")
    password = form.get("password", "")
    if len(username) > 64 or len(password) > 1024:
        return await run_in_threadpool(login_page, request, error="账号或密码错误。", status=401, destination=destination)
    client_id = request.client.host if request.client else "unknown"
    result = await run_in_threadpool(auth.login, username, password, client_id)
    if result["status"] != "ok":
        messages = {"invalid": ("账号或密码错误。", 401),
                    "throttled": ("尝试次数过多，请在 15 分钟后重试。", 429),
                    "unconfigured": ("管理员账号尚未配置，请联系管理员。", 503)}
        message, status = messages[result["status"]]
        response = await run_in_threadpool(login_page, request, error=message, status=status,
                                          username=username, destination=destination)
        if status == 429:
            response.headers["Retry-After"] = "900"
        return response
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(SESSION_COOKIE, result["token"], max_age=SESSION_SECONDS,
                        httponly=True, secure=cookie_secure(request), samesite="lax")
    response.delete_cookie(LOGIN_CSRF_COOKIE, secure=cookie_secure(request), httponly=True, samesite="lax")
    return response


@app.post("/logout")
async def logout_post(request: Request):
    form = await bounded_form(request)
    if not valid_csrf(request, form.get("csrf_token", ""), request.state.user["csrf_token"]):
        raise HTTPException(status_code=403, detail="无效的退出请求，请刷新页面后重试")
    await run_in_threadpool(auth.logout, request.cookies.get(SESSION_COOKIE, ""))
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, secure=cookie_secure(request), httponly=True, samesite="lax")
    return response


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
    return templates.TemplateResponse(request=request, name="index.html", context={"user": request.state.user})


@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    return templates.TemplateResponse(request=request, name="logs.html", context={"user": request.state.user})


@app.get("/api/logs")
def run_logs(day: str | None = None, status: str = "all", page: int = Query(1, ge=1),
             page_size: int = Query(50, ge=1, le=100)):
    if status not in ("all", "success", "failed", "running"):
        raise HTTPException(status_code=400, detail="无效的运行状态")
    return reports.run_log_report(day_arg(day), status, page, page_size)


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
