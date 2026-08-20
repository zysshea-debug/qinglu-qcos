"""青鹭收银系统 - 生产服务启动器 (Safety Pack V1 修复)

职责:
  - 以真正的前台常驻方式启动 Flask app
    (werkzeug.serving.make_server + serve_forever，阻塞主线程)
  - 将本项目服务 PID 写入 runtime/qcos.pid，服务运行期间持续存在
  - 仅当服务真正停止（信号 / 进程退出 / 正常关闭）时才清理 PID
  - 日志写入 logs/qcos_server.log
  - 只管理本项目进程；绝不 taskkill /IM python.exe，绝不误杀其他 Python

用法:
  venv\\Scripts\\python.exe qcos_server.py
"""
import atexit
import os
import signal
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_DIR = PROJECT_ROOT / "logs"
PID_FILE = RUNTIME_DIR / "qcos.pid"
LOG_FILE = LOG_DIR / "qcos_server.log"


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
            # errors="ignore" 兼容中文 Windows 下 tasklist 的非 UTF-8 输出
            import subprocess
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, errors="ignore", timeout=10,
            ).stdout
            return out is not None and str(pid) in out
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        # 任何异常（进程不存在 / 解码失败 / 权限问题）都视为不存活
        return False


def _write_pid(pid):
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _remove_pid():
    """仅在服务真正停止时调用（atexit / 显式关闭）。"""
    try:
        if PID_FILE.exists():
            PID_FILE.unlink()
    except OSError:
        pass


def _log(line):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {line}\n")
    except OSError:
        pass


def _run():
    _ensure_dirs()

    # ---- 单实例检查：已有有效 PID 则拒绝重复启动 ----
    existing = _read_existing_pid()
    if existing:
        _log(f"检测到已存在的 QCOS 服务 PID={existing}，拒绝重复启动。")
        print(f"QCOS_ALREADY_RUNNING PID={existing}")
        print("如确认该进程已失效，请删除 runtime\\qcos.pid 后重试。")
        return 1

    import app as app_module
    import werkzeug.serving as _ws

    pid = os.getpid()
    _write_pid(pid)
    # PID 文件在服务运行期间持续存在；仅当进程真正退出时才清理
    atexit.register(_remove_pid)
    _log(f"QCOS server starting, PID={pid}")
    print(f"QCOS_SERVER_START PID={pid}")

    host = os.environ.get("QCOS_HOST", "0.0.0.0")
    port = int(os.environ.get("QCOS_PORT", "5000"))

    # 显式创建 WSGI 服务器并以阻塞方式运行（不使用开发服务器 reloader）
    server = _ws.make_server(host, port, app_module.app, threaded=True)
    server.daemon_threads = False

    stop_event = threading.Event()

    def _handle_signal(signum, _frame):
        _log(f"收到信号 {signum}，正在关闭服务...")
        stop_event.set()
        # 在独立线程中关闭，避免信号处理上下文中死锁
        threading.Thread(target=server.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
    except (ValueError, OSError):
        # 非主线程等不支持场景：忽略，仍由 serve_forever 阻塞维持服务
        pass

    try:
        # 前台常驻：主线程阻塞在 serve_forever，直到收到关闭信号
        server.serve_forever()
    except KeyboardInterrupt:
        _log("KeyboardInterrupt，正在关闭服务。")
    finally:
        stop_event.set()
        try:
            server.shutdown()
        except Exception:
            pass
        _log("QCOS server stopped.")
    # atexit 会在进程真正退出时清理 runtime/qcos.pid
    return 0


if __name__ == "__main__":
    sys.exit(_run())
