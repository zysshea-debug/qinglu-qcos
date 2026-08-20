"""启动入口一致性测试 (Safety Pack V1 收尾)

验证生产启动 BAT 已全部统一到 qcos_server.py，
且不存在硬编码开发机路径、watchdog、重复自启任务等遗留问题。

运行:
  pytest tests/test_launch_entries.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8", errors="ignore")


def _all_bats():
    return list(ROOT.glob("*.bat"))


def test_launch_bat_uses_qcos_server():
    """启动.bat 最终必须调用 qcos_server.py"""
    assert "qcos_server.py" in _read("启动.bat")


def test_launch_bat_uses_venv_python():
    """启动.bat 必须使用项目 venv 的 python.exe"""
    assert "venv\\Scripts\\python.exe" in _read("启动.bat")


def test_launch_bat_no_python_app():
    """启动.bat 不得直接 python app.py"""
    assert "python app.py" not in _read("启动.bat")


def test_launch_bat_no_workbuddy():
    """启动.bat 不得含 .workbuddy 硬编码"""
    assert ".workbuddy" not in _read("启动.bat")


def test_run_qcos_no_workbuddy():
    """run_qcos.bat 不得含开发机 .workbuddy 路径"""
    assert ".workbuddy" not in _read("run_qcos.bat")


def test_run_qcos_no_goto_loop():
    """run_qcos.bat 不得保留 watchdog 无限循环"""
    assert "goto loop" not in _read("run_qcos.bat")


def test_run_qcos_calls_start_bat():
    """run_qcos.bat 作为兼容包装器最终调用 启动.bat"""
    assert "启动.bat" in _read("run_qcos.bat")


def test_register_no_qcos_autostart():
    """注册开机自启.bat 不得再创建 QCOS_AutoStart 第二套任务"""
    assert "QCOS_AutoStart" not in _read("注册开机自启.bat")


def test_register_calls_install_autostart():
    """注册开机自启.bat 应转调 install_autostart.bat"""
    assert "install_autostart.bat" in _read("注册开机自启.bat")


def test_uninstall_has_qcos_server():
    """卸载脚本需清理正式任务 QCOS_Server"""
    assert "QCOS_Server" in _read("uninstall_autostart.bat")


def test_uninstall_has_qcos_autostart():
    """卸载脚本需兼容清理历史遗留 QCOS_AutoStart"""
    assert "QCOS_AutoStart" in _read("uninstall_autostart.bat")


def test_production_bats_no_taskkill_im_python():
    """所有生产 BAT 不得包含 taskkill /IM python.exe（禁止误杀全部 Python）"""
    for p in _all_bats():
        content = p.read_text(encoding="utf-8", errors="ignore")
        assert "taskkill /IM python.exe" not in content, f"危险命令出现在 {p.name}"
