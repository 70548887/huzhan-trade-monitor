"""Fetch the public homepage feed, validate it, and store observations."""
from __future__ import annotations

import argparse
import errno
import hashlib
import json
import logging
import os
import re
import time
import unicodedata
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from . import db, settings

LOG = logging.getLogger("huzhan")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; HuzhanMonitor/1.0)",
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
}


class FeedError(Exception):
    """The public source did not return the expected data."""


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").split())


def money_to_cents(text: str) -> int:
    clean = normalize(text).replace(",", "").replace("￥", "").replace("¥", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", clean):
        raise FeedError(f"金额格式异常: {text[:80]}")
    try:
        value = Decimal(clean) * 100
        return int(value)
    except (InvalidOperation, ValueError):
        raise FeedError(f"金额格式异常: {text[:80]}") from None


def clean_url(raw: str | None, *, avatar: bool = False) -> str | None:
    if not raw:
        return None
    parsed = urlsplit(urljoin(settings.HOME_URL, raw.strip()))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    # Avatar image transformation queries do not identify a different buyer.
    query = "" if avatar else parsed.query
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, query, ""))


def fingerprint(row: dict) -> str:
    fields = [
        row["product_url"] or normalize(row["title"]),
        normalize(row["buyer"]), row["avatar_url"] or "", row["amount_cents"],
    ]
    data = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def parse_homepage(html: str) -> tuple[dict, dict]:
    soup = BeautifulSoup(html, "html.parser")
    metas = {str(x.get("name", "")).lower(): str(x.get("content", ""))
             for x in soup.find_all("meta")}
    if "pg-code" not in metas or "item-number" not in metas:
        raise FeedError("首页缺少交易请求参数，可能遇到验证页面或页面结构变化")
    index = soup.select_one(".scroll-index")
    if not index:
        raise FeedError("首页缺少互站指数，可能遇到验证页面或页面结构变化")
    totals = {}
    for cite in index.select("cite"):
        label = normalize(cite.get_text(" ", strip=True))
        value = cite.find("i")
        if not value:
            continue
        if label.startswith("成交"):
            totals["total_cents"] = money_to_cents(value.get_text(strip=True))
        elif label.startswith("交易"):
            count = normalize(value.get_text(strip=True)).replace(",", "")
            if not count.isdigit():
                raise FeedError("首页累计交易笔数格式异常")
            totals["transaction_count"] = int(count)
    if set(totals) != {"total_cents", "transaction_count"}:
        raise FeedError("首页累计成交额或交易笔数缺失")
    return {"vcode": metas["pg-code"], "number": metas["item-number"]}, totals


def parse_feed(body: str) -> list[dict]:
    if not body or not body.strip():
        raise FeedError("交易列表为空，不能确认是零交易或数据异常")
    soup = BeautifulSoup(body, "html.parser")
    nodes = soup.find_all("li")
    if not nodes:
        raise FeedError("交易列表结构异常")
    rows = []
    for li in nodes:
        a, buyer, amount, title, status = (
            li.find("a"), li.find("strong"), li.find("em"), li.find("span"), li.find("i")
        )
        if not all((a, buyer, amount, title, status)):
            raise FeedError("交易条目字段不完整，整批采集已停止")
        title_text = normalize(title.get_text(" ", strip=True))
        buyer_text = normalize(buyer.get_text(" ", strip=True))
        status_text = normalize(status.get_text(" ", strip=True)).strip("[]【】 ")
        if not title_text or not buyer_text or not status_text:
            raise FeedError("交易条目存在空字段")
        status_code = {"正在交易": "pending", "交易成功": "success"}.get(status_text, "unknown")
        image = li.find("img")
        row = {
            "title": title_text, "buyer": buyer_text,
            "amount_cents": money_to_cents(amount.get_text(strip=True)),
            "status": status_code, "status_text": status_text,
            "product_url": clean_url(a.get("href")),
            "avatar_url": clean_url(image.get("src") if image else None, avatar=True),
        }
        row["fingerprint"] = fingerprint(row)
        rows.append(row)
    return rows


def request_with_retry(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    for attempt in range(3):
        try:
            response = client.request(method, url, **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise FeedError("网络请求失败")


@contextmanager
def collection_lock():
    """Use an operating-system lock to prevent concurrent collectors."""
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = settings.DATA_DIR / "collector.lock"
    with lock_path.open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                yield False
                return
            raise
        try:
            yield True
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def setup_logging() -> None:
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=settings.LOG_DIR / "collector.log", encoding="utf-8", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def collect() -> int:
    setup_logging()
    with collection_lock() as acquired:
        if not acquired:
            print("已有采集任务运行，本次跳过。")
            LOG.warning("collector already running; skipped")
            return 0
        db.initialize()
        started = settings.stamp()
        run_id = db.begin_run(started)
        homepage_bytes = response_bytes = None
        try:
            with httpx.Client(
                headers=HEADERS, timeout=30, follow_redirects=True,
            ) as client:
                homepage = request_with_retry(client, "GET", settings.HOME_URL)
                homepage_bytes = homepage.content
                params, platform = parse_homepage(homepage.text)
                response = request_with_retry(
                    client, "POST", settings.FEED_URL,
                    data={"list": "Latestdeal", **params},
                    headers={"Referer": settings.HOME_URL, "X-Requested-With": "XMLHttpRequest"},
                )
                response_bytes = response.content
                try:
                    payload = response.json()
                except ValueError:
                    raise FeedError("交易接口未返回有效 JSON") from None
                if not isinstance(payload, dict) or not isinstance(payload.get("body"), str):
                    raise FeedError("交易接口响应结构异常")
                rows = parse_feed(payload["body"])
            observed_at = settings.stamp()
            result = db.record_success(run_id, observed_at, rows, platform, homepage_bytes, response_bytes)
            LOG.info("采集成功 run=%s result=%s", run_id, result)
            print(f"采集成功：{result['rows']} 行，{result['unique']} 个条目，重复 {result['duplicates']} 行"
                  + ("；已建立首次基线。" if result["baseline"] else "。"))
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            db.record_failure(run_id, settings.stamp(), detail, homepage_bytes, response_bytes)
            LOG.exception("采集失败 run=%s", run_id)
            print(f"采集失败：{detail}")
            return 1
        try:
            db.backup_and_prune()
            db.evaluate_trial()
            db.maintenance_status(None)
        except Exception as exc:
            db.maintenance_status(f"{type(exc).__name__}: {exc}")
            LOG.exception("备份或试运行评估失败")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="采集互站公开交易")
    parser.parse_args()
    raise SystemExit(collect())


if __name__ == "__main__":
    main()
