"""启动入口一致性 + qcos_server 生命周期测试 (Safety Pack V1 收尾修复)

验证:
  - 生产启动 BAT 全部统一到 qcos_server.py，且 run_qcos.bat 不再依赖中文文件名
  - qcos_server.py 作为前台常驻服务运行，运行时 PID 持续存在、仅在真正退出时清理
  - 单实例 / 失效 PID 清理能力保留

运行:
  pytest tests/test_launch_entries.py
"""
import atexit
import os
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

import qcos_server  # 纯函数/类定义，module 级 import 无副作用


def _read_bat(name):
    return (ROOT / name).read_text(encoding="utf-8", errors="ignore")


def _all_bats():
    return list(ROOT.glob("*.bat"))


# ===================== 启动入口 BAT 检查 =====================

def test_launch_bat_uses_qcos_server():
    assert "qcos_server.py" in _read_bat("启动.bat")


def test_launch_bat_uses_venv_python():
    assert "venv\\Scripts\\python.exe" in _read_bat("启动.bat")


def test_launch_bat_no_python_app():
    assert "python app.py" not in _read_bat("启动.bat")


def test_launch_bat_no_workbuddy():
    assert ".workbuddy" not in _read_bat("启动.bat")


def test_run_qcos_no_workbuddy():
    assert ".workbuddy" not in _read_bat("run_qcos.bat")


def test_run_qcos_no_goto_loop():
    assert "goto loop" not in _read_bat("run_qcos.bat")


def test_run_qcos_no_start_bat_reference():
    """run_qcos.bat 不得再调用中文文件名 启动.bat（CMD 编码乱码根源）"""
    assert "启动.bat" not in _read_bat("run_qcos.bat")


def test_run_qcos_direct_qcos_server():
    """run_qcos.bat 直接调用项目 venv 的 qcos_server.py"""
    content = _read_bat("run_qcos.bat")
    assert "qcos_server.py" in content
    assert "venv\\Scripts\\python.exe" in content


def test_run_qcos_no_python_app():
    assert "python app.py" not in _read_bat("run_qcos.bat")


def test_register_no_qcos_autostart():
    assert "QCOS_AutoStart" not in _read_bat("注册开机自启.bat")


def test_register_calls_install_autostart():
    assert "install_autostart.bat" in _read_bat("注册开机自启.bat")


def test_uninstall_has_qcos_server():
    assert "QCOS_Server" in _read_bat("uninstall_autostart.bat")


def test_uninstall_has_qcos_autostart():
    assert "QCOS_AutoStart" in _read_bat("uninstall_autostart.bat")


def test_production_bats_no_taskkill_im_python():
    for p in _all_bats():
        content = p.read_text(encoding="utf-8", errors="ignore")
        assert "taskkill /IM python.exe" not in content, f"危险命令出现在 {p.name}"


# ===================== qcos_server 生命周期 =====================

class _FakeServer:
    def __init__(self, app, host, port):
        self.app = app
        self.host = host
        self.port = port
        self.threaded = None
        self.serving = False
        self._stop = threading.Event()

    def serve_forever(self):
        self.serving = True
        self._stop.wait()  # 阻塞直到 shutdown

    def shutdown(self):
        self._stop.set()


@pytest.fixture
def server_env(tmp_path, monkeypatch):
    # 隔离 runtime/log，绝不触碰真实项目
    monkeypatch.setattr(qcos_server, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(qcos_server, "LOG_DIR", tmp_path)
    monkeypatch.setattr(qcos_server, "PID_FILE", tmp_path / "qcos.pid")
    monkeypatch.setattr(qcos_server, "LOG_FILE", tmp_path / "qcos_server.log")

    # 注入假 app 模块（无需真实 Flask / DB）
    fake_app = type(sys)("app")
    fake_app.app = object()
    monkeypatch.setitem(sys.modules, "app", fake_app)

    captured = {}
    def _fake_make_server(host, port, app, threaded=False):
        srv = _FakeServer(app, host, port)
        srv.threaded = threaded
        captured["server"] = srv
        return srv
    monkeypatch.setattr("werkzeug.serving.make_server", _fake_make_server)

    # 让 PID 存活判定可控：仅当前进程 PID 视为存活，避免测试触发真实 tasklist
    monkeypatch.setattr(qcos_server, "_pid_alive", lambda pid: pid == os.getpid())

    # 仅记录 atexit 注册，不真正注册到进程退出（避免测试结束时误删真实 PID 文件）
    registered = {"funcs": []}
    def _fake_atexit(func, *a, **k):
        registered["funcs"].append(func)
    monkeypatch.setattr(atexit, "register", _fake_atexit)

    return tmp_path, captured, registered


def _wait_serving(captured, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        srv = captured.get("server")
        if srv is not None and srv.serving:
            return True
        time.sleep(0.05)
    return False


def test_server_blocks_and_pid_persists(server_env):
    """服务运行期间 PID 持续存在，且 serve_forever 真正进入阻塞式生命周期。"""
    tmp, captured, registered = server_env
    pid_file = qcos_server.PID_FILE

    t = threading.Thread(target=qcos_server._run, daemon=True)
    t.start()
    assert _wait_serving(captured), "serve_forever 必须进入并保持阻塞"

    # 1) 服务运行期间 PID 必须存在
    assert pid_file.exists(), "服务运行期间 runtime/qcos.pid 必须存在"
    # 2) 显式阻塞式服务器（非开发 reloader 立即返回）
    assert captured["server"].serving is True
    # 3) atexit 已注册 PID 清理（仅进程真退出时执行）
    assert any(f is qcos_server._remove_pid for f in registered["funcs"]), \
        "应注册 atexit，在进程真正退出时清理 PID"

    # 单实例：存在有效 PID 时第二次调用必须拒绝
    rc = qcos_server._run()
    assert rc == 1, "已有有效 PID 时应拒绝重复启动"

    # 正常停止
    captured["server"].shutdown()
    t.join(timeout=5)
    assert not t.is_alive(), "服务线程应在 shutdown 后退出"


def test_pid_cleaned_on_real_exit(server_env):
    """_remove_pid 能正确删除 PID 文件（对应进程真正退出时 atexit 的清理动作）。"""
    tmp, captured, registered = server_env
    pid_file = qcos_server.PID_FILE

    t = threading.Thread(target=qcos_server._run, daemon=True)
    t.start()
    assert _wait_serving(captured)
    assert pid_file.exists()

    # 模拟进程真正退出时 atexit 触发
    qcos_server._remove_pid()
    assert not pid_file.exists(), "_remove_pid 应删除 PID 文件"

    captured["server"].shutdown()
    t.join(timeout=5)


def test_stale_pid_is_cleaned_and_starts(server_env):
    """失效 PID（进程不存在）应被自动清理并允许启动新服务。"""
    tmp, captured, registered = server_env
    pid_file = qcos_server.PID_FILE
    pid_file.write_text("999999", encoding="utf-8")  # 不可能存活的 PID

    t = threading.Thread(target=qcos_server._run, daemon=True)
    t.start()
    assert _wait_serving(captured), "失效 PID 应被清理并启动新服务"
    assert pid_file.exists()

    captured["server"].shutdown()
    t.join(timeout=5)


def test_single_instance_blocks_with_valid_pid(server_env):
    """已有有效 PID（活进程）时拒绝启动，输出 QCOS_ALREADY_RUNNING 语义。"""
    tmp, captured, registered = server_env
    pid_file = qcos_server.PID_FILE
    pid_file.write_text(str(os.getpid()), encoding="utf-8")  # 当前进程存活

    rc = qcos_server._run()
    assert rc == 1, "有效 PID 对应活进程时应拒绝启动"


def test_make_server_called_blocking_no_reloader(server_env):
    """启动使用显式阻塞式服务器（threaded），不使用开发服务器 reloader。"""
    tmp, captured, registered = server_env
    t = threading.Thread(target=qcos_server._run, daemon=True)
    t.start()
    assert _wait_serving(captured)
    srv = captured["server"]
    assert srv is not None
    assert srv.threaded is True  # 并发处理请求
    captured["server"].shutdown()
    t.join(timeout=5)
