"""青鹭收银系统 - 生产数据库安全备份模块 (Safety Pack V1)

- 以 SQLite Online Backup API 优先备份（不直接 cp 运行中的库文件）
- 备份文件: backups/qcos_YYYYMMDD_HHMMSS.db
- 完成后校验: 存在 / size>0 / 可打开 / PRAGMA integrity_check = ok
- 默认仅保留最近 30 份自动备份，更旧的被删除
- 绝不修改源数据库

用法:
    python backup_qcos.py            # 默认保留 30 份
    python backup_qcos.py --keep 60  # 保留 60 份
"""
import os
import re
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "qcos.db"
BACKUP_DIR = PROJECT_ROOT / "backups"
DEFAULT_KEEP = 30


def _parse_keep(argv):
    """解析 --keep N 参数，非法则报错退出。"""
    keep = DEFAULT_KEEP
    args = list(argv)
    i = 0
    while i < len(args):
        if args[i] == "--keep":
            if i + 1 >= len(args):
                raise ValueError("--keep 需要一个数字参数，例如 --keep 30")
            try:
                keep = int(args[i + 1])
            except ValueError:
                raise ValueError(f"--keep 参数必须是数字，收到: {args[i + 1]!r}")
            if keep < 1:
                raise ValueError("--keep 至少为 1")
            i += 2
        else:
            raise ValueError(f"未知参数: {args[i]!r}（支持: --keep N）")
    return keep


def backup_database(db_path=None, backup_dir=None, keep=None):
    """执行一次安全备份，返回 (backup_path, size_bytes)。

    - db_path / backup_dir 可注入（测试用），默认生产路径
    - 源库缺失 -> FileNotFoundError
    - 备份校验失败 -> 抛异常，调用方负责非0退出
    """
    db_path = Path(db_path) if db_path else DB_PATH
    backup_dir = Path(backup_dir) if backup_dir else BACKUP_DIR
    keep = keep if keep is not None else DEFAULT_KEEP

    if not db_path.exists():
        raise FileNotFoundError(f"源数据库不存在: {db_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)

    # 文件名: qcos_YYYYMMDD_HHMMSS.db
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"qcos_{stamp}.db"

    # ===== 优先: SQLite Online Backup API（安全，不影响源库）=====
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    # ===== 校验 =====
    if not backup_path.exists():
        raise RuntimeError(f"备份文件未生成: {backup_path}")
    size = backup_path.stat().st_size
    if size <= 0:
        raise RuntimeError(f"备份文件大小为 0，视为失败: {backup_path}")
    check = sqlite3.connect(str(backup_path))
    try:
        row = check.execute("PRAGMA integrity_check").fetchone()
        if row is None or row[0] != "ok":
            raise RuntimeError(f"备份完整性校验失败: {row}")
    finally:
        check.close()

    # ===== 保留最近 keep 份 =====
    pattern = re.compile(r"^qcos_\d{8}_\d{6}\.db$")
    existing = sorted(
        (p for p in backup_dir.iterdir() if p.is_file() and pattern.match(p.name)),
        key=lambda p: p.name,
        reverse=True,
    )
    for stale in existing[keep:]:
        try:
            stale.unlink()
        except OSError:
            pass  # 删除旧备份失败不阻塞主流程（不影响本次备份成功）

    return backup_path, size


def main(argv=None):
    argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        keep = _parse_keep(argv)
    except ValueError as e:
        print(f"BACKUP_FAILED")
        print(f"ERROR={e}")
        return 2

    try:
        backup_path, size = backup_database(keep=keep)
    except Exception as e:
        print("BACKUP_FAILED")
        print(f"ERROR={e}")
        return 1

    print("BACKUP_SUCCESS")
    print(f"BACKUP_PATH={backup_path}")
    print(f"BACKUP_SIZE={size}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
