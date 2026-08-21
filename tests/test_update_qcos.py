"""update_qcos 纯逻辑测试（mock Git / subprocess / HTTP / PID）

绝不执行真实 git pull、不碰真实 qcos.db、不启动真实服务。
所有与外部世界的交互都被替换为可控的假对象。
"""
import os
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import update_qcos

# _prepare() 会替换的模块级引用快照：测试退出时必须恢复原值，
# 否则污染后续测试（历史根因：_start_server 等未恢复，导致
# test_start_server_uses_qcos_server 拿到 lambda 而报"未调用启动命令"）。
_PATCHED_ATTRS = (
    "PROJECT_ROOT", "_backup", "_migrate", "_smoke",
    "_stop_old_server", "_start_server", "_read_pid", "_health_check",
)
_ORIG_ATTRS = {name: getattr(update_qcos, name) for name in _PATCHED_ATTRS}

PASS = 0
FAIL = 0


def ok(name):
    global PASS
    PASS += 1
    print(f"[OK] {name}")


def fail(name, detail=""):
    global FAIL
    FAIL += 1
    print(f"[FAIL] {name} {detail}")


# ======================= 假对象 =======================

class _Result:
    """模拟 subprocess.CompletedProcess"""

    def __init__(self, code=0, stdout="", stderr=""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr


class _FakeProc:
    def __init__(self, pid=9999):
        self.pid = pid


class FakeRunner:
    """可编程假 runner: 按命令关键字匹配预设结果，记录全部调用。"""

    def __init__(self):
        self.calls = []
        self.results = {}  # key 子串 -> _Result
        self.default = _Result(0, "", "")
        self.popen_result = _FakeProc()

    def run(self, args, cwd=None, **kw):
        self.calls.append(list(args))
        joined = " ".join(args)
        for key, res in self.results.items():
            if key in joined:
                return res
        return self.default

    def popen(self, args, **kw):
        self.calls.append(list(args))
        return self.popen_result


def _make_project_root():
    """构造临时项目骨架（qcos.db/.env/.git/venv），返回 Path。"""
    td = tempfile.mkdtemp(prefix="qcos_upd_")
    root = Path(td)
    (root / "qcos.db").write_bytes(b"x")
    (root / ".env").write_text("QCOS_SECRET_KEY=test\n")
    (root / ".git").mkdir()
    (root / "venv").mkdir()
    return root


# ======================= 基础函数测试 =======================

def test_check_basic():
    """关键文件检查：缺失任一 -> 失败"""
    root = _make_project_root()
    old_root = update_qcos.PROJECT_ROOT
    update_qcos.PROJECT_ROOT = root
    try:
        ok_ret, msg = update_qcos._check_basic()
        if not ok_ret:
            return fail("关键文件齐全应通过", msg)
    finally:
        update_qcos.PROJECT_ROOT = old_root

    root2 = _make_project_root()
    (root2 / ".env").unlink()  # 缺 .env
    update_qcos.PROJECT_ROOT = root2
    try:
        ok_ret, msg = update_qcos._check_basic()
        if ok_ret:
            return fail("缺 .env 应失败", "误判通过")
        if "缺少关键生产文件" not in msg:
            return fail("缺文件应提示", msg)
    finally:
        update_qcos.PROJECT_ROOT = old_root
    ok("关键文件检查（齐全通过 / 缺失报错）")


def test_requirements_changed():
    """requirements.txt 变化判断"""
    fake = FakeRunner()
    fake.results["git diff"] = _Result(0, "requirements.txt\napp.py\n", "")
    if not update_qcos._requirements_changed("a" * 40, runner=fake):
        return fail("requirements 变化应返回 True")
    fake2 = FakeRunner()
    fake2.results["git diff"] = _Result(0, "", "")
    if update_qcos._requirements_changed("a" * 40, runner=fake2):
        return fail("requirements 未变化应返回 False")
    if update_qcos._requirements_changed("", runner=fake2):
        return fail("无旧 commit 应保守返回 False")
    ok("requirements.txt 变化判断")


def test_is_dirty():
    """git dirty 判断"""
    fake = FakeRunner()
    fake.results["git status"] = _Result(0, " M app.py\n", "")
    if not update_qcos._is_dirty(runner=fake):
        return fail("dirty 应返回 True")
    fake2 = FakeRunner()
    fake2.results["git status"] = _Result(0, "", "")
    if update_qcos._is_dirty(runner=fake2):
        return fail("clean 应返回 False")
    ok("git dirty 判断")


# ======================= run_update 流程测试 =======================

@contextmanager
def _prepare(project_root=None):
    """构造测试环境: patch PROJECT_ROOT 与各外部函数，产出 fake runner。

    必须作为上下文管理器使用（with _prepare() as fake:）。退出时（含异常）
    自动把 8 个被替换的模块级引用恢复为导入时快照，保证测试间隔离。
    """
    if project_root is None:
        project_root = _make_project_root()
    update_qcos.PROJECT_ROOT = project_root
    fake = FakeRunner()
    # 默认 git 全部成功，clean
    fake.default = _Result(0, "", "")
    # backup / migrate / smoke / 服务操作全部替换为无害假实现
    update_qcos._backup = lambda runner=None: str(project_root / "backups" / "qcos_20260820_000000.db")
    update_qcos._migrate = lambda: None
    update_qcos._smoke = lambda runner=None: "SMOKE_CHECK_PASS"
    update_qcos._stop_old_server = lambda runner=None: None
    update_qcos._start_server = lambda runner=None: _FakeProc(8888)
    update_qcos._read_pid = lambda: 8888
    update_qcos._health_check = lambda port=5000, timeout_seconds=30, runner=None: True
    try:
        yield fake
    finally:
        for name, orig in _ORIG_ATTRS.items():
            setattr(update_qcos, name, orig)


def test_dirty_aborts():
    """dirty 工作区 -> 停止并报 LOCAL_CODE_CHANGES_DETECTED"""
    with _prepare() as fake:
        fake.results["git status --porcelain"] = _Result(0, " M app.py\n", "")
        try:
            update_qcos.run_update(runner=fake)
            fail("dirty 应中断", "未抛异常")
        except RuntimeError as e:
            if "LOCAL_CODE_CHANGES_DETECTED" not in str(e):
                return fail("dirty 中断信息", str(e))
            ok("dirty 工作区中断 (LOCAL_CODE_CHANGES_DETECTED)")


def test_backup_failure_aborts():
    """备份失败 -> UPDATE_ABORTED_BACKUP_FAILED 并停止"""
    with _prepare() as fake:
        def bad_backup(runner=None):
            raise RuntimeError("backup boom")
        update_qcos._backup = bad_backup
        try:
            update_qcos.run_update(runner=fake)
            fail("备份失败应中断", "未抛异常")
        except RuntimeError as e:
            if "UPDATE_ABORTED_BACKUP_FAILED" not in str(e):
                return fail("备份失败中断信息", str(e))
            ok("备份失败中断 (UPDATE_ABORTED_BACKUP_FAILED)")


def test_fetch_failure_aborts():
    """git fetch 失败 -> 停止"""
    with _prepare() as fake:
        fake.results["git rev-parse HEAD"] = _Result(0, "a" * 40 + "\n", "")
        fake.results["git fetch"] = _Result(1, "", "fatal: unable to access")
        try:
            update_qcos.run_update(runner=fake)
            fail("fetch 失败应中断", "未抛异常")
        except RuntimeError as e:
            if "git fetch 失败" not in str(e):
                return fail("fetch 失败信息", str(e))
            ok("git fetch 失败中断")


def test_pull_failure_aborts():
    """git pull --ff-only 失败 -> 停止（不回滚）"""
    with _prepare() as fake:
        fake.results["git rev-parse HEAD"] = _Result(0, "a" * 40 + "\n", "")
        fake.results["git rev-parse origin/main"] = _Result(0, "b" * 40 + "\n", "")
        fake.results["git pull"] = _Result(1, "", "fatal: not fast-forward")
        try:
            update_qcos.run_update(runner=fake)
            fail("pull 失败应中断", "未抛异常")
        except RuntimeError as e:
            if "git pull --ff-only 失败" not in str(e):
                return fail("pull 失败信息", str(e))
            ok("git pull 失败中断（不做任何回滚）")


def test_smoke_failure_aborts():
    """smoke check 失败 -> 停止并报 UPDATE_FAILED_AFTER_PULL"""
    with _prepare() as fake:
        def bad_smoke(runner=None):
            raise RuntimeError("SMOKE_CHECK_FAIL")
        update_qcos._smoke = bad_smoke
        # 已是最新（remote == HEAD）路径也会执行 smoke
        fake.results["git rev-parse HEAD"] = _Result(0, "a" * 40 + "\n", "")
        fake.results["git rev-parse origin/main"] = _Result(0, "a" * 40 + "\n", "")
        try:
            update_qcos.run_update(runner=fake)
            fail("smoke 失败应中断", "未抛异常")
        except RuntimeError as e:
            if "UPDATE_FAILED_AFTER_PULL" not in str(e):
                return fail("smoke 失败信息", str(e))
            ok("smoke 失败中断 (UPDATE_FAILED_AFTER_PULL)")


def test_health_check_failure_aborts():
    """健康检查失败 -> 停止"""
    with _prepare() as fake:
        def bad_health(port=5000, timeout_seconds=30, runner=None):
            raise RuntimeError("health boom")
        update_qcos._health_check = bad_health
        fake.results["git rev-parse HEAD"] = _Result(0, "a" * 40 + "\n", "")
        fake.results["git rev-parse origin/main"] = _Result(0, "b" * 40 + "\n", "")
        try:
            update_qcos.run_update(runner=fake)
            fail("健康检查失败应中断", "未抛异常")
        except RuntimeError as e:
            if "UPDATE_FAILED_AFTER_PULL" not in str(e):
                return fail("健康检查失败信息", str(e))
            ok("健康检查失败中断")


def test_already_up_to_date():
    """无更新 -> ALREADY_UP_TO_DATE，不执行 pull"""
    with _prepare() as fake:
        fake.results["git rev-parse HEAD"] = _Result(0, "a" * 40 + "\n", "")
        fake.results["git rev-parse origin/main"] = _Result(0, "a" * 40 + "\n", "")
        info = update_qcos.run_update(runner=fake)
        if not info.get("already_up_to_date"):
            return fail("应识别已是最新")
        if any("git pull" in " ".join(c) for c in fake.calls):
            return fail("已是最新不应执行 pull")
        if info.get("status") != "ALREADY_UP_TO_DATE":
            return fail("状态应为 ALREADY_UP_TO_DATE", str(info.get("status")))
        ok("ALREADY_UP_TO_DATE（不执行无意义 pull）")


def test_full_success():
    """完整成功流程 + 输出关键字段 + 无危险命令"""
    with _prepare() as fake:
        fake.results["git rev-parse HEAD"] = _Result(0, "a" * 40 + "\n", "")
        fake.results["git rev-parse origin/main"] = _Result(0, "b" * 40 + "\n", "")
        fake.results["git pull"] = _Result(0, "", "")
        info = update_qcos.run_update(runner=fake)

        if info.get("status") != "UPDATE_SUCCESS":
            return fail("UPDATE_SUCCESS", str(info))
        if info.get("old_commit") != "a" * 40:
            return fail("OLD_COMMIT", str(info.get("old_commit")))
        if info.get("server_pid") != 8888:
            return fail("SERVER_PID", str(info.get("server_pid")))
        if not info.get("health_check"):
            return fail("HEALTH_CHECK", str(info.get("health_check")))
        if "backups" not in info.get("backup_path", ""):
            return fail("BACKUP_PATH", str(info.get("backup_path")))

        # 危险命令审计
        all_cmds = [" ".join(c) for c in fake.calls]
        for bad in ["reset --hard", "clean -fd", "stash", "checkout --force", "pull --force"]:
            if any(bad in c for c in all_cmds):
                return fail(f"出现危险命令: {bad}", str(all_cmds))
        # 必须使用 --ff-only
        if not any("pull" in c and "--ff-only" in c for c in all_cmds):
            return fail("pull 必须 --ff-only", str(all_cmds))
        ok("完整成功流程 + 危险命令审计")


def test_no_hardcoded_user_paths():
    """源码不得写死 C:\\Users\\PC 或 C:\\Users\\zyssh"""
    src = Path(update_qcos.__file__).read_text(encoding="utf-8")
    for bad in ["C:\\Users\\PC", "C:\\Users\\zyssh", "C:/Users/PC", "C:/Users/zyssh"]:
        if bad in src:
            return fail(f"源码包含硬编码路径: {bad}")
    if "__file__" not in src or "resolve().parent" not in src:
        return fail("PROJECT_ROOT 未基于 __file__")
    ok("无硬编码用户路径，PROJECT_ROOT 基于 __file__")


def test_start_server_uses_qcos_server():
    """更新后新服务必须通过项目 venv 启动 qcos_server.py（不再 app.py）。"""
    fake = FakeRunner()
    update_qcos._start_server(runner=fake)
    if not fake.calls:
        return fail("未调用启动命令")
    cmd = " ".join(fake.calls[-1])
    if "qcos_server.py" not in cmd:
        return fail("新服务应启动 qcos_server.py", cmd)
    if "app.py" in cmd:
        return fail("新服务不应直接启动 app.py", cmd)
    ok("更新后新服务启动 qcos_server.py")


if __name__ == "__main__":
    test_check_basic()
    test_requirements_changed()
    test_is_dirty()
    test_dirty_aborts()
    test_backup_failure_aborts()
    test_fetch_failure_aborts()
    test_pull_failure_aborts()
    test_smoke_failure_aborts()
    test_health_check_failure_aborts()
    test_already_up_to_date()
    test_full_success()
    test_no_hardcoded_user_paths()
    test_start_server_uses_qcos_server()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
