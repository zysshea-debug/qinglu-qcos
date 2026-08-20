"""青鹭收银系统 - 生产服务启动器 (Safety Pack V1)

职责:
  - 启动现有 Flask app (app.app)
  - 将本项目服务 PID 写入 runtime/qcos.pid
  - 服务退出时尽力清理 PID 文件
  - 日志写入 logs/qcos_server.log
  - 只管理本项目进程；绝不 taskkill /IM python.exe，绝不杀其他 Python 进程

用法:
  venv\\Scripts\\python.exe qcos_server.py
"""
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_DIR = PROJECT_ROOT / "logs"
PID_FILE = RUNTIME_DIR / "qcos.pid"
LOG_FILE = LOG_DIR / "qcos_server.log"

# 生产启动必须使用项目自己的 venv（由 BAT / update_qcos.py 调用方保证）
# 本文件仅要求使用当前解释器，不写死任何绝对路径


def _ensure_dirs():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _read_existing_pid():
    """读取已有 PID 文件；进程真实存在则返回 pid，否则返回 None（并清理失效文件）。"""
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        return None
    if _pid_alive(pid):
        return pid
    # 失效 PID：自动清理
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    return None


def _pid_alive(pid):
    """跨平台判断进程是否存活。仅读，不杀进程。"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            # Windows: 通过 tasklist 过滤 PID，不 kill 任何进程
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return str(pid) in out
        else:
            os.kill(pid, 0)
            return True
    except (OSError, subprocess.SubprocessError):
        return False


def _write_pid(pid):
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _log(line):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {line}\n")


def _run():
    _ensure_dirs()

    existing = _read_existing_pid()
    if existing:
        _log(f"检测到已存在的 QCOS 服务 PID={existing}，拒绝重复启动。")
        print(f"QCOS_ALREADY_RUNNING PID={existing}")
        print("如确认该进程已失效，请删除 runtime\\qcos.pid 后重试。")
        return 1

    import app as app_module  # 复用现有 Flask app

    pid = os.getpid()
    _write_pid(pid)
    _log(f"QCOS server starting, PID={pid}")
    print(f"QCOS_SERVER_START PID={pid}")

    try:
        app_module.app.run(
            host="0.0.0.0",
            port=int(os.environ.get("QCOS_PORT", "5000")),
            debug=False,
            use_reloader=False,  # 生产禁用 reloader（否则子进程 PID 与 pid 文件不一致）
        )
    finally:
        _log("QCOS server exiting, cleaning PID file.")
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(_run())
