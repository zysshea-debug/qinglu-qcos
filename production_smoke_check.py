"""青鹭收银系统 - 生产环境只读 Smoke Check (Safety Pack V1)

检查:
  1. config 可 import
  2. models 可 import
  3. app 可 import
  4. qcos.db 存在
  5. SQLite 可打开
  6. PRAGMA integrity_check = ok
  7. 核心表存在: players / sessions / session_players / users / settings

绝对禁止写入生产数据 —— 本脚本只做 SELECT/PRAGMA 类只读操作。

成功输出: SMOKE_CHECK_PASS
失败: 输出错误明细并返回非0退出码。
"""
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# 核心表清单（存在即视为 schema 就绪）
CORE_TABLES = ["players", "sessions", "session_players", "users", "settings"]

# 若被测试直接调用，可注入（tests/test_production_smoke.py 使用临时项目结构）
_ENV_DB_PATH = os.environ.get("QCOS_SMOKE_DB_PATH", "")


def run_smoke_check(db_path=None):
    """执行只读 smoke check，返回 (ok: bool, messages: list[str])。"""
    messages = []

    # 1. 源码可导入（config / models / app）
    sys.path.insert(0, str(PROJECT_ROOT))
    try:
        import config  # noqa: F401
        messages.append("OK  import config")
    except Exception as e:
        messages.append(f"FAIL import config: {e}")
        return False, messages
    try:
        import models  # noqa: F401
        messages.append("OK  import models")
    except Exception as e:
        messages.append(f"FAIL import models: {e}")
        return False, messages
    try:
        import app  # noqa: F401
        messages.append("OK  import app")
    except Exception as e:
        messages.append(f"FAIL import app: {e}")
        return False, messages

    # 2. 数据库存在
    target = Path(db_path) if db_path else Path(_ENV_DB_PATH) if _ENV_DB_PATH else (PROJECT_ROOT / "qcos.db")
    if not target.exists():
        messages.append(f"FAIL 数据库不存在: {target}")
        return False, messages
    messages.append(f"OK  数据库存在: {target}")

    # 3. 可打开 + integrity_check
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    except Exception as e:
        messages.append(f"FAIL 无法以只读方式打开数据库: {e}")
        return False, messages
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            messages.append(f"FAIL integrity_check = {row}")
            return False, messages
        messages.append("OK  PRAGMA integrity_check = ok")
    except Exception as e:
        messages.append(f"FAIL integrity_check 执行失败: {e}")
        return False, messages
    finally:
        conn.close()

    # 4. 核心表存在
    try:
        conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        tables = {r[0] for r in rows}
        conn.close()
    except Exception as e:
        messages.append(f"FAIL 读取表清单失败: {e}")
        return False, messages

    missing = [t for t in CORE_TABLES if t not in tables]
    if missing:
        messages.append(f"FAIL 缺失核心表: {missing}")
        return False, messages
    messages.append(f"OK  核心表齐全: {', '.join(CORE_TABLES)}")

    return True, messages


def main():
    ok, messages = run_smoke_check()
    for m in messages:
        print(m)
    if ok:
        print("SMOKE_CHECK_PASS")
        return 0
    print("SMOKE_CHECK_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
