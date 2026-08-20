"""青鹭收银系统 - 一键安全更新脚本 (Safety Pack V1)

严格流程（任一步失败立即停止，保留数据库与备份，绝不自动 reset/回滚）:

  1. 定位 PROJECT_ROOT（脚本自身目录）
  2. 确认 qcos.db / .env / .git / venv 存在
  3. 先执行安全备份（backup_qcos.py）
  4. 检查 git status --porcelain，有真实代码修改则停止
  5. 记录 OLD_COMMIT
  6. git fetch origin main
  7. 比较 origin/main 与 HEAD，相同则 ALREADY_UP_TO_DATE
  8. git pull --ff-only origin main（禁止 reset --hard / clean -fd / force / stash）
  9. 判断 OLD_COMMIT..HEAD 中 requirements.txt 是否变化，变化则重装依赖
 10. 复用官方 init_db() 做 schema 安全迁移（保留数据，不写测试数据）
 11. 运行 production_smoke_check.py
 12. 根据 runtime/qcos.pid 只停止本项目服务
 13. venv 启动 qcos_server.py
 14. GET http://127.0.0.1:5000/login -> 200 = HEALTH_CHECK PASS
 15. 输出 UPDATE_SUCCESS / OLD_COMMIT / NEW_COMMIT / BACKUP_PATH / SERVER_PID / HEALTH_CHECK

用法:
  venv\\Scripts\\python.exe update_qcos.py
"""
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
VENV_PY = PROJECT_ROOT / "venv" / "Scripts" / "python.exe"
QINGLU_REPO_NAME = "qinglu-qcos"  # 仅用于 STEP1 目录名校验（不写死用户路径）

FAIL_PULL = "UPDATE_FAILED_AFTER_PULL"      # pull 已成功但后续失败
ABORT_BACKUP = "UPDATE_ABORTED_BACKUP_FAILED"  # 备份失败
ABORT_DIRTY = "LOCAL_CODE_CHANGES_DETECTED"    # 有本地代码修改


# ======================= 可注入点（测试用） =======================
class _Runner:
    """包装 subprocess 调用，测试时可整体 mock。"""

    def run(self, args, **kw):
        return subprocess.run(args, capture_output=True, text=True, **kw)

    def popen(self, args, **kw):
        return subprocess.Popen(args, **kw)


_runner = _Runner()


def _check_basic():
    """STEP2: 关键生产文件检查。返回 (ok, message)。"""
    required = {
        "qcos.db": PROJECT_ROOT / "qcos.db",
        ".env": PROJECT_ROOT / ".env",
        ".git": PROJECT_ROOT / ".git",
        "venv": PROJECT_ROOT / "venv",
    }
    missing = [name for name, p in required.items() if not p.exists()]
    if missing:
        return False, f"缺少关键生产文件: {', '.join(missing)}"
    return True, "生产文件齐全"


def _git(args, runner=None):
    """执行 git 命令，返回 (returncode, stdout, stderr)。"""
    runner = runner or _runner
    r = runner.run(["git", *args], cwd=str(PROJECT_ROOT))
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _is_dirty(runner=None):
    """STEP4: 检查 git status --porcelain 是否有真实代码修改。

    生产文件（qcos.db / .env / backups/ / logs/ / runtime/）已被 .gitignore 排除，
    正常情况下不会出现在 status 中。任何出现在 porcelain 输出中的条目都视为真实修改。
    """
    runner = runner or _runner
    code, out, err = _git(["status", "--porcelain"], runner=runner)
    if code != 0:
        raise RuntimeError(f"git status 失败: {err}")
    return bool(out.strip())


def _requirements_changed(old_commit, runner=None):
    """STEP9: 判断 OLD_COMMIT..HEAD 中 requirements.txt 是否有变化。

    无法获取旧 commit 时（如无历史）保守返回 False（不触发重装，避免误伤）。
    """
    runner = runner or _runner
    if not old_commit:
        return False
    code, out, err = _git(
        ["diff", "--name-only", f"{old_commit}..HEAD", "--", "requirements.txt"],
        runner=runner,
    )
    if code != 0:
        return False
    return bool(out.strip())


def _backup(runner=None):
    """STEP3: 执行安全备份（复用 backup_qcos.py 模块）。"""
    runner = runner or _runner
    sys.path.insert(0, str(PROJECT_ROOT))
    import backup_qcos
    path, size = backup_qcos.backup_database()
    return path


def _fetch(runner=None):
    """STEP6: git fetch origin main。"""
    code, out, err = _git(["fetch", "origin", "main"], runner=runner)
    if code != 0:
        raise RuntimeError(f"git fetch 失败: {err}")


def _origin_main_commit(runner=None):
    """STEP7: 获取 origin/main 的 commit hash。"""
    code, out, err = _git(["rev-parse", "origin/main"], runner=runner)
    if code != 0 or not out:
        raise RuntimeError(f"无法读取 origin/main: {err or '空输出'}")
    return out.splitlines()[0]


def _pull_ff_only(runner=None):
    """STEP8: git pull --ff-only origin main。绝不使用危险命令。"""
    code, out, err = _git(["pull", "--ff-only", "origin", "main"], runner=runner)
    if code != 0:
        raise RuntimeError(f"git pull --ff-only 失败: {err or out}")


def _install_deps(runner=None):
    """STEP9b: 使用项目 venv pip 安装依赖。"""
    runner = runner or _runner
    r = runner.run(
        [str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt"],
        cwd=str(PROJECT_ROOT),
    )
    if r.returncode != 0:
        raise RuntimeError(f"依赖安装失败: {r.stderr.strip() or r.stdout.strip()}")


def _migrate():
    """STEP10: 复用现有官方 init_db()（幂等：CREATE TABLE IF NOT EXISTS + ALTER 安全迁移）。

    说明（只读勘察确认）:
      - models.init_db() 负责建表 + 既有安全增量迁移（ALTER TABLE 带列存在性检查），
        不写测试数据、不重置已有管理员密码（_ensure_default_admin 仅在无 admin 时创建）。
      - 因此这里直接复用，不自创第二套 migration。
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    from models import init_db
    init_db()


def _smoke(runner=None):
    """STEP11: 运行 production_smoke_check.py。"""
    runner = runner or _runner
    r = runner.run(
        [sys.executable, str(PROJECT_ROOT / "production_smoke_check.py")],
        cwd=str(PROJECT_ROOT),
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"smoke check 失败: {r.stdout.strip() or r.stderr.strip()}"
        )
    return r.stdout.strip()


def _pid_file():
    return PROJECT_ROOT / "runtime" / "qcos.pid"


def _read_pid():
    """读取 PID 文件，返回 int 或 None。"""
    pid_file = _pid_file()
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _pid_alive(pid):
    """只读判断进程是否存活（不 kill 任何进程）。"""
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _stop_old_server(runner=None):
    """STEP12: 只根据 runtime/qcos.pid 停止本项目 QCOS 服务。

    - PID 不存在/已失效: 视为服务未运行，不报错
    - 只 kill 本项目记录的那个 PID，绝不 kill 其他 Python
    - Windows 下用 taskkill /PID <pid>（不带 /IM，不匹配全名）
    """
    runner = runner or _runner
    pid = _read_pid()
    if pid is None:
        return None
    if not _pid_alive(pid):
        try:
            _pid_file().unlink()
        except OSError:
            pass
        return None
    if os.name == "nt":
        r = runner.run(["taskkill", "/PID", str(pid), "/F"], cwd=str(PROJECT_ROOT))
        if r.returncode != 0:
            raise RuntimeError(f"停止 QCOS 服务失败 (PID={pid}): {r.stderr.strip() or r.stdout.strip()}")
    else:
        import signal
        os.kill(pid, signal.SIGTERM)
    return pid


def _start_server(runner=None):
    """STEP13: 用项目 venv 启动 qcos_server.py，返回 Popen 对象。"""
    runner = runner or _runner
    proc = runner.popen(
        [str(VENV_PY), str(PROJECT_ROOT / "qcos_server.py")],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc


def _health_check(port=5000, timeout_seconds=30, runner=None):
    """STEP14: GET http://127.0.0.1:<port>/login，200 即通过。"""
    url = f"http://127.0.0.1:{port}/login"
    deadline = time.time() + timeout_seconds
    last_err = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except Exception as e:  # noqa: BLE001 - 服务可能还在启动中
            last_err = e
            time.sleep(1)
    raise RuntimeError(f"健康检查失败 {url}: {last_err or '超时'}")


# ======================= 主流程 =======================

def run_update(port=5000, runner=None):
    """执行完整更新流程。

    返回 (status, info_dict)；异常向上抛出由 main 统一报告。
    """
    info = {}
    runner = runner or _runner

    # STEP1 定位 PROJECT_ROOT（本文件所在目录），目录名不做硬编码校验
    # （仅在目录名确实为 qinglu-qcos 时打印提示，不存在则不检查，保证路径通用）
    info["project_root"] = str(PROJECT_ROOT)

    # STEP2 生产文件检查
    ok, msg = _check_basic()
    if not ok:
        raise RuntimeError(msg)

    # STEP3 安全备份
    try:
        backup_path = _backup(runner=runner)
    except Exception as e:
        info["backup_error"] = str(e)
        raise RuntimeError(f"{ABORT_BACKUP}: {e}")
    info["backup_path"] = str(backup_path)

    # STEP4 dirty 检查
    try:
        dirty = _is_dirty(runner=runner)
    except Exception as e:
        raise RuntimeError(f"git status 检查失败: {e}")
    if dirty:
        raise RuntimeError(ABORT_DIRTY)

    # STEP5 OLD_COMMIT
    code, old, err = _git(["rev-parse", "HEAD"], runner=runner)
    if code != 0 or not old:
        raise RuntimeError(f"无法读取 HEAD commit: {err or '空输出'}")
    old_commit = old.splitlines()[0]
    info["old_commit"] = old_commit

    # STEP6-7 fetch + 比较
    _fetch(runner=runner)
    remote_commit = _origin_main_commit(runner=runner)
    info["new_commit"] = remote_commit

    if remote_commit == old_commit:
        # 已是最新：不做无意义 pull，允许继续 smoke/健康检查
        info["already_up_to_date"] = True
    else:
        info["already_up_to_date"] = False
        # STEP8 pull --ff-only
        try:
            _pull_ff_only(runner=runner)
        except Exception as e:
            raise RuntimeError(f"git pull --ff-only 失败: {e}")

        # pull 后重新读 HEAD 作为 NEW_COMMIT
        code, new_head, err = _git(["rev-parse", "HEAD"], runner=runner)
        if code == 0 and new_head:
            info["new_commit"] = new_head.splitlines()[0]

        # STEP9 requirements 变化判断
        if _requirements_changed(old_commit, runner=runner):
            _install_deps(runner=runner)
            info["deps_installed"] = True
        else:
            info["deps_installed"] = False

    # STEP10 数据库安全迁移（复用官方 init_db）
    try:
        _migrate()
    except Exception as e:
        raise RuntimeError(f"数据库迁移失败（已保留数据，勿自动回滚）: {e}")
    info["migration"] = "ok"

    # STEP11 smoke check（已是最新时也执行，作为健康检查）
    try:
        smoke_out = _smoke(runner=runner)
    except Exception as e:
        raise RuntimeError(f"{FAIL_PULL}（smoke 失败）: {e}")
    info["smoke"] = smoke_out

    # 已是最新且服务已在运行：不重复启动
    if info.get("already_up_to_date"):
        existing_pid = _read_pid()
        info["server_pid"] = existing_pid if existing_pid else "already-running"
        info["health_check"] = _health_check(port=port, runner=runner)
        info["status"] = "ALREADY_UP_TO_DATE"
        return info

    # STEP12 停止旧服务（只针对本项目 PID）
    try:
        stopped = _stop_old_server(runner=runner)
    except Exception as e:
        raise RuntimeError(f"{FAIL_PULL}（停止旧服务失败）: {e}")
    info["stopped_pid"] = stopped

    # STEP13 启动新服务
    try:
        proc = _start_server(runner=runner)
    except Exception as e:
        raise RuntimeError(f"{FAIL_PULL}（启动新服务失败）: {e}")
    info["proc"] = proc

    # 等待 pid 文件出现，取真实服务 PID
    server_pid = None
    deadline = time.time() + 15
    while time.time() < deadline:
        server_pid = _read_pid()
        if server_pid:
            break
        time.sleep(0.5)
    info["server_pid"] = server_pid

    # STEP14 健康检查
    try:
        health = _health_check(port=port, runner=runner)
    except Exception as e:
        raise RuntimeError(f"{FAIL_PULL}（健康检查失败）: {e}")
    info["health_check"] = health

    info["status"] = "UPDATE_SUCCESS"
    return info


def main():
    try:
        info = run_update()
    except Exception as e:
        msg = str(e)
        if FAIL_PULL in msg:
            # pull 后失败：输出 UPDATE_FAILED_AFTER_PULL + 关键信息（不自动回滚）
            print("UPDATE_FAILED_AFTER_PULL")
            print(f"ERROR={msg}")
            try:
                code, head, _ = _git(["rev-parse", "HEAD"])
                if code == 0:
                    print(f"CURRENT_COMMIT={head.splitlines()[0]}")
            except Exception:
                pass
            # backup path 尽力补输出
            pid = _read_pid()
            print(f"BACKUP_PATH=见上方备份日志")
            print("提示: 未自动 git reset / 未回滚数据库，请人工决定处理方式。")
        else:
            print("UPDATE_FAILED")
            print(f"ERROR={msg}")
        return 1

    print("UPDATE_SUCCESS")
    print(f"OLD_COMMIT={info.get('old_commit', '')}")
    print(f"NEW_COMMIT={info.get('new_commit', info.get('old_commit', ''))}")
    print(f"BACKUP_PATH={info.get('backup_path', '')}")
    print(f"SERVER_PID={info.get('server_pid', '')}")
    print(f"HEALTH_CHECK={'PASS' if info.get('health_check') else 'N/A'}")
    if info.get("already_up_to_date"):
        print("NOTE=ALREADY_UP_TO_DATE（未执行 pull，已通过健康检查）")
    if info.get("deps_installed"):
        print("DEPS=INSTALLED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
