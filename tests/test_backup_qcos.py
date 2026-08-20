"""backup_qcos 模块测试（使用临时目录 + 临时 sqlite，绝不碰生产 qcos.db）"""
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import backup_qcos

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


def _make_source_db(path, n=5):
    """创建临时源库并写入数据，返回行数。"""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)")
    for i in range(n):
        conn.execute("INSERT INTO t (val) VALUES (?)", (f"v{i}",))
    conn.commit()
    conn.close()
    return n


def _make_old_backups(bdir, count):
    """预置 count 份旧备份（命名符合 qcos_YYYYMMDD_HHMMSS.db 模式）。"""
    for i in range(count):
        stamp = f"20200101_{i:06d}"  # 排序稳定的历史时间
        (bdir / f"qcos_{stamp}.db").write_bytes(b"old")


def test_normal_backup():
    """1. 正常创建备份 + 2. integrity + 3. size>0 + 4. 源数据不变"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src.db"
        _make_source_db(src, 5)
        bdir = td / "backups"

        path, size = backup_qcos.backup_database(db_path=src, backup_dir=bdir, keep=30)

        if not path.exists():
            return fail("正常备份: 文件存在", str(path))
        if size <= 0:
            return fail("正常备份: size>0", f"size={size}")
        conn = sqlite3.connect(str(path))
        row = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        if row[0] != "ok":
            return fail("正常备份: integrity_check=ok", str(row))
        # 源数据不变
        conn = sqlite3.connect(str(src))
        cnt = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        vals = conn.execute("SELECT val FROM t ORDER BY id").fetchall()
        conn.close()
        if cnt != 5:
            return fail("源数据不变: 行数", f"cnt={cnt}")
        if [r[0] for r in vals] != [f"v{i}" for i in range(5)]:
            return fail("源数据不变: 内容", str(vals))
        ok("正常备份 + integrity + size + 源数据不变")


def test_no_source_fails():
    """5. 无源库时抛 FileNotFoundError"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        try:
            backup_qcos.backup_database(
                db_path=td / "nope.db", backup_dir=td / "b", keep=30
            )
            fail("无源库应失败", "未抛异常")
        except FileNotFoundError:
            ok("无源库失败(FileNotFoundError)")


def test_keep_30_and_cleanup():
    """6. 保留最近30份 + 7. 旧备份正确清理"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src.db"
        _make_source_db(src, 3)
        bdir = td / "backups"
        bdir.mkdir()
        _make_old_backups(bdir, 35)  # 35 份旧备份

        path, _ = backup_qcos.backup_database(db_path=src, backup_dir=bdir, keep=30)

        remaining = sorted(p.name for p in bdir.glob("qcos_*.db"))
        if len(remaining) != 30:
            return fail("保留30份", f"剩余 {len(remaining)} 份: {remaining[:5]}...")
        if path.name not in remaining:
            return fail("保留30份: 最新备份应保留", path.name)
        # 最旧的 6 份（000000~000005）应被删除，000006 起应保留
        for old in ("qcos_20200101_000000.db", "qcos_20200101_000005.db"):
            if old in remaining:
                return fail("旧备份清理: 应删除", old)
        if "qcos_20200101_000006.db" not in remaining:
            return fail("旧备份清理: 应保留第30份", remaining[:3])
        ok("保留最近30份 + 清理旧备份")


def test_keep_custom():
    """--keep 参数: 保留指定份数"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src.db"
        _make_source_db(src, 2)
        bdir = td / "backups"
        bdir.mkdir()
        _make_old_backups(bdir, 12)
        backup_qcos.backup_database(db_path=src, backup_dir=bdir, keep=5)
        remaining = list(bdir.glob("qcos_*.db"))
        if len(remaining) != 5:
            return fail("--keep 5", f"剩余 {len(remaining)} 份")
        ok("--keep 5 生效")


def test_invalid_keep():
    """--keep 参数非法时 _parse_keep 抛 ValueError"""
    for bad in (["--keep", "abc"], ["--keep", "0"], ["--keep"], ["--nope"]):
        try:
            backup_qcos._parse_keep(bad)
            fail(f"非法参数应报错: {bad}")
        except ValueError:
            pass
    ok("非法 --keep 参数被拒绝")


def test_cli_success_output():
    """CLI main() 成功输出 BACKUP_SUCCESS / BACKUP_PATH / BACKUP_SIZE"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = td / "src.db"
        _make_source_db(src, 1)

        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        # main() 内部使用模块级 DB_PATH/BACKUP_DIR；这里直接 monkeypatch 模块常量
        old_db, old_dir = backup_qcos.DB_PATH, backup_qcos.BACKUP_DIR
        backup_qcos.DB_PATH = src
        backup_qcos.BACKUP_DIR = td / "b"
        try:
            with redirect_stdout(buf):
                rc = backup_qcos.main(["--keep", "10"])
        finally:
            backup_qcos.DB_PATH, backup_qcos.BACKUP_DIR = old_db, old_dir
        out = buf.getvalue()
        if rc != 0:
            return fail("CLI 退出码=0", f"rc={rc}\n{out}")
        if "BACKUP_SUCCESS" not in out:
            return fail("CLI 输出 BACKUP_SUCCESS", out)
        if "BACKUP_PATH=" not in out or "BACKUP_SIZE=" not in out:
            return fail("CLI 输出 PATH/SIZE", out)
        ok("CLI 成功输出 (BACKUP_SUCCESS/PATH/SIZE)")


if __name__ == "__main__":
    test_normal_backup()
    test_no_source_fails()
    test_keep_30_and_cleanup()
    test_keep_custom()
    test_invalid_keep()
    test_cli_success_output()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
