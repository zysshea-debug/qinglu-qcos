"""增量导入微信信息
只读取 Excel 玩家档案表的「昵称」「微信号」「微信备注」三列，
按昵称匹配更新 players 表的 wechat / wechat_remark，不触碰其他数据。
"""
import os
import sys
import shutil
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH
from python_calamine import CalamineWorkbook

EXCEL_PATH = r"C:\Users\zyssh\Desktop\QCOS_青鹭玩家数据库_V2.0 - 副本.xlsx"
SHEET_NAME = "玩家档案"
HEADER_ROW = 3  # 第 4 行为表头（0-based）
DATA_START_ROW = 4


def str_val(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return str(v)
    return str(v).strip()


def main():
    if not os.path.exists(EXCEL_PATH):
        print(f"错误: 找不到 Excel 文件: {EXCEL_PATH}")
        sys.exit(1)

    # 备份生产库
    bak = DB_PATH + ".bak_wechat_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copyfile(DB_PATH, bak)
    print(f"已备份数据库: {bak}")

    wb = CalamineWorkbook.from_path(EXCEL_PATH)
    ws = wb.get_sheet_by_name(SHEET_NAME)
    data = ws.to_python()

    headers = data[HEADER_ROW]
    col_map = {str(h).strip(): i for i, h in enumerate(headers) if h}

    required = ["昵称", "微信号", "微信备注"]
    missing = [c for c in required if c not in col_map]
    if missing:
        print(f"错误: Excel 缺少列: {missing}")
        sys.exit(1)

    updates = []
    for r in range(DATA_START_ROW, len(data)):
        row = data[r]
        if not row or not row[0]:
            continue
        nickname = str_val(row[col_map["昵称"]])
        wechat = str_val(row[col_map["微信号"]])
        remark = str_val(row[col_map["微信备注"]])
        if not nickname:
            continue
        updates.append((wechat, remark, nickname))

    print(f"Excel 中读取到 {len(updates)} 条玩家微信信息")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    # 用昵称匹配，只更新有变化的记录
    now = datetime.now().isoformat()
    updated = 0
    unchanged = 0
    not_found = 0
    multi = 0

    for wechat, remark, nickname in updates:
        # 先查是否唯一
        rows = db.execute(
            "SELECT id, name, wechat, wechat_remark FROM players WHERE name=? COLLATE NOCASE",
            [nickname],
        ).fetchall()
        if not rows:
            not_found += 1
            print(f"  未找到玩家: {nickname}")
            continue
        if len(rows) > 1:
            multi += 1
            print(f"  昵称重复({len(rows)}人): {nickname}，跳过")
            continue

        p = rows[0]
        if p["wechat"] == wechat and p["wechat_remark"] == remark:
            unchanged += 1
            continue

        db.execute(
            "UPDATE players SET wechat=?, wechat_remark=?, updated_at=? WHERE id=?",
            [wechat, remark, now, p["id"]],
        )
        updated += 1

    db.commit()

    # 统计
    total = db.execute("SELECT COUNT(*) c FROM players").fetchone()["c"]
    empty_wechat = db.execute(
        "SELECT COUNT(*) c FROM players WHERE wechat IS NULL OR wechat=''"
    ).fetchone()["c"]
    empty_remark = db.execute(
        "SELECT COUNT(*) c FROM players WHERE wechat_remark IS NULL OR wechat_remark=''"
    ).fetchone()["c"]

    print(f"\n导入完成:")
    print(f"  Excel 行数: {len(updates)}")
    print(f"  更新: {updated}")
    print(f"  无变化: {unchanged}")
    print(f"  未找到: {not_found}")
    print(f"  昵称重复跳过: {multi}")
    print(f"\n数据库统计:")
    print(f"  玩家总数: {total}")
    print(f"  微信号为空: {empty_wechat}")
    print(f"  微信备注为空: {empty_remark}")

    db.close()


if __name__ == "__main__":
    main()
