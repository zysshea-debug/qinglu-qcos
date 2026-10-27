"""青鹭收银系统 - 桌台治理安全迁移脚本 (P0 2026-10)

用途：将 machines 表按 config.MACHINES 定义同步（改名 + 新增第5张桌 + 加 area/active 列）。
- 先调用 backup_qcos 做带时间戳安全备份（SQLite Online Backup，含完整性校验）
- 再运行 models.init_db() 仅确保 schema 就绪（init_db 不再自动迁移 machines）
- 随后显式调用 apply_machine_migration() 执行幂等迁移（按稳定 id UPDATE/INSERT，绝不删除重建）
- 打印迁移前后 machines 快照并断言关键不变量
- 可重复执行（幂等）

用法（在部署根目录，需 venv）：
    venv\\Scripts\\python.exe migrate_tables.py

回滚：用 backups/qcos_YYYYMMDD_HHMMSS.db 覆盖 qcos.db 即可（备份文件名含时间戳）。
"""
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
import models
import backup_qcos


def main():
    db_path = config.DB_PATH
    if not os.path.exists(db_path):
        print("MIGRATE_ABORTED: 源数据库不存在:", db_path)
        return 2

    # 1) 安全备份（带时间戳 + 完整性校验）
    try:
        backup_path, size = backup_qcos.backup_database(
            db_path=db_path, backup_dir=PROJECT_ROOT / "backups")
        print("BACKUP_SUCCESS:", backup_path, f"({size} bytes)")
    except Exception as e:
        print("MIGRATE_ABORTED: 备份失败:", e)
        return 1

    # 2) 先确保 schema 就绪（init_db 不再自动迁移 machines）
    models.init_db()

    # 2b) 显式执行桌台迁移（已先备份）：按稳定 id UPDATE/INSERT，绝不删除重建
    db = models.get_db()
    try:
        models.apply_machine_migration(db)
        print("MIGRATE_OK")
    except Exception as e:
        print("MIGRATE_FAILED:", e)
        print("ROLLBACK: 用", backup_path, "覆盖", db_path)
        return 1
    finally:
        db.close()

    # 3) 校验关键不变量
    import sqlite3
    c = sqlite3.connect(str(db_path)); c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT id,name,type,area,active,sort_order FROM machines ORDER BY id"
    ).fetchall()
    print("MACHINES_AFTER:")
    for r in rows:
        print("  ", dict(r))
    ids = [r['id'] for r in rows]
    assert ids[:5] == [1, 2, 3, 4, 5], f"id 应为 [1,2,3,4,5]，实际 {ids}"
    assert rows[4]['name'] == '楼上V4八口', rows[4]['name']
    assert all(r['active'] == 1 for r in rows), "默认应全部启用"
    print("VERIFY_OK: 5 machines, ids 不变, 第5张桌「楼上V4八口」已新增")
    c.close()
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
