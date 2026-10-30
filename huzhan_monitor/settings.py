from datetime import datetime, timedelta, timezone
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("HUZHAN_MONITOR_DATA_DIR", ROOT / "data")).resolve()
LOG_DIR = Path(os.environ.get("HUZHAN_MONITOR_LOG_DIR", ROOT / "logs")).resolve()
DB_PATH = DATA_DIR / "monitor.sqlite3"
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
HOME_URL = "https://www.huzhan.com/"
FEED_URL = HOME_URL + "apage/"
PORT = 8878
RAW_RETENTION_DAYS = 90
BACKUP_RETENTION_COUNT = 30
COOKIE_SECURE = os.environ.get("HUZHAN_COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")
AUTO_COLLECT = os.environ.get("HUZHAN_AUTO_COLLECT", "1").strip().lower() not in ("0", "false", "no", "off")


def now_beijing() -> datetime:
    return datetime.now(BEIJING)


def stamp(value: datetime | None = None) -> str:
    return (value or now_beijing()).astimezone(BEIJING).isoformat(timespec="seconds")
