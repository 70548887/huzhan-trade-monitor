"""Local-only administrator setup and password reset."""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import sqlite3
from pathlib import Path

from . import auth, settings


def _write_credentials(path: Path, username: str, password: str) -> None:
    """Create a new credential file without overwriting existing user files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(f"username: {username}\npassword: {password}\n")
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="在服务器本机创建或重置互站监控管理员")
    parser.add_argument("--username", default="admin", help="管理员用户名，默认 admin")
    parser.add_argument("--reset", action="store_true", help="重置已有管理员并撤销全部登录会话")
    parser.add_argument("--generate", action="store_true", help="生成随机密码，保存到仅本地可读的新文件")
    parser.add_argument("--output", type=Path, help="随机凭据文件路径，默认 data/login-credentials.txt")
    args = parser.parse_args()
    if args.output is not None and not args.generate:
        parser.error("--output 需要同时指定 --generate")

    created_file: Path | None = None
    try:
        auth.initialize()
        if auth.is_configured() and not args.reset:
            raise auth.AdminAlreadyConfigured("管理员已存在；确认重置时请添加 --reset")
        username = args.username.strip()
        if args.generate:
            password = secrets.token_urlsafe(24)
            # Validate before creating the credential file or changing the account.
            auth._validate_credentials(username, password)
            output = (args.output or settings.DATA_DIR / "login-credentials.txt").resolve()
            _write_credentials(output, username, password)
            created_file = output
        else:
            password = getpass.getpass("管理员密码（至少 12 个字符）：")
            repeated = getpass.getpass("再次输入密码：")
            if password != repeated:
                raise ValueError("两次输入的密码不一致")

        auth.set_admin(username, password, reset=args.reset)
    except (ValueError, OSError, sqlite3.Error) as exc:
        if created_file is not None:
            created_file.unlink(missing_ok=True)
        parser.exit(1, f"管理员配置失败：{exc}\n")
    except BaseException:
        if created_file is not None:
            created_file.unlink(missing_ok=True)
        raise

    print("管理员已配置；此前登录的会话均已失效。")
    if created_file is not None:
        print(f"随机凭据已保存到：{created_file}")
        print("请在本机读取并妥善保存，避免将该文件上传到仓库。")


if __name__ == "__main__":
    main()
