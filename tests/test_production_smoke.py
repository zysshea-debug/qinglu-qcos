"""production_smoke_check 测试（只使用临时库/临时项目结构，绝不碰生产 qcos.db）

关键点: 在 import production_smoke_check 前先把 config.DB_PATH 指向临时库，
这样其内部 `import app` 触发的模块级 init_db() 也只会作用于临时库。
"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault("QCOS_SECRET_KEY", "test_secret_key_qcos_unit_test")
os.environ.setdefault("QCOS_ADMIN_PASSWORD", "test_admin_pw_qcos_v2")

import config
# 强制 config.DB_PATH 指向临时库，使 app 模块级 init_db() 不会触碰真实 qcos.db
_tmp_db = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
_tmp_db.close()
config.DB_PATH = _tmp_db.name

import models  # noqa: E402
models.init_db()  # 在临时库建出核心表

import production_smoke_check as psc  # noqa: E402

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


def test_pass_with_core_tables():
    """核心表齐全 -> 通过"""
    ok_ret, msgs = psc.run_smoke_check(db_path=_tmp_db.name)
    if not ok_ret:
        return fail("核心表齐全应通过", "; ".join(msgs))
    ok("smoke check 通过（临时库核心表齐全）")


def test_missing_table_fails():
    """缺核心表 -> 失败"""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as f:
        tmp = f.name
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    try:
        ok_ret, msgs = psc.run_smoke_check(db_path=tmp)
        if ok_ret:
            return fail("缺核心表应失败", "误判通过")
        if not any("缺失核心表" in m for m in msgs):
            return fail("缺核心表应提示表名", str(msgs))
        ok("缺失核心表检测生效")
    finally:
        os.unlink(tmp)


def test_no_db_fails():
    """数据库不存在 -> 失败"""
    ok_ret, msgs = psc.run_smoke_check(db_path="/nonexistent/path/qcos.db")
    if ok_ret:
        return fail("无库应失败", "误判通过")
    if not any("数据库不存在" in m for m in msgs):
        return fail("无库应提示", str(msgs))
    ok("无源库检测生效")


def test_does_not_write_source():
    """smoke check 不写源库（对比前后行数/表数一致）"""
    conn = sqlite3.connect(_tmp_db.name)
    before_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    conn.close()

    psc.run_smoke_check(db_path=_tmp_db.name)

    conn = sqlite3.connect(_tmp_db.name)
    after_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    conn.close()
    if before_tables != after_tables:
        return fail("smoke check 不应写库", f"{before_tables}->{after_tables}")
    ok("smoke check 只读（表数量不变）")


if __name__ == "__main__":
    test_pass_with_core_tables()
    test_missing_table_fails()
    test_no_db_fails()
    test_does_not_write_source()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
