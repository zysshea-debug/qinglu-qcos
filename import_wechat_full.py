"""全量导入微信信息（去重后 upsert）
- 读取 Excel 玩家档案表的「昵称」「微信号」「微信备注」三列
- 按 微信号 -> 昵称 -> 微信备注 三键匹配系统玩家：
  * 匹配到 -> 更新 wechat / wechat_remark（Excel 为准，空值不覆盖已有值）
  * 匹配不到 -> 新建玩家（name 优先微信备注，其次微信号）
- 导入前自动备份 qcos.db
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
        print(f"错误: 找不到 Excel: {EXCEL_PATH}")
        sys.exit(1)

    # 备份
    bak = DB_PATH + ".bak_full_wechat_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copyfile(DB_PATH, bak)
    print(f"已备份: {bak}")

    wb = CalamineWorkbook.from_path(EXCEL_PATH)
    ws = wb.get_sheet_by_name(SHEET_NAME)
    data = ws.to_python()
    header_row = next(i for i, row in enumerate(data) if row and '昵称' in row)
    cm = {str(h).strip(): i for i, h in enumerate(data[header_row]) if h}

    # 收集并去重（按微信号；微信号空用 昵称|备注）
    excel_rows = []
    seen = set()
    for r in range(header_row + 1, len(data)):
        row = data[r]
        if not row:
            continue
        nickname = str_val(row[cm['昵称']])
        wechat = str_val(row[cm['微信号']])
        remark = str_val(row[cm['微信备注']])
        if not (nickname or wechat or remark):
            continue
        key = wechat if wechat else f"{nickname}|{remark}"
        if key in seen:
            continue
        seen.add(key)
        excel_rows.append((nickname, wechat, remark))

    print(f"Excel 去重后记录: {len(excel_rows)}")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")

    players = db.execute(
        "SELECT id, name, qcos_id, wechat, wechat_remark FROM players"
    ).fetchall()
    # 建索引便于匹配
    by_wechat = {}
    for p in players:
        if p['wechat']:
            by_wechat.setdefault(p['wechat'].strip().lower(), []).append(p)
    by_name = {}
    for p in players:
        by_name.setdefault(p['name'].strip().lower(), []).append(p)
    by_remark = {}
    for p in players:
        if p['wechat_remark']:
            by_remark.setdefault(p['wechat_remark'].strip().lower(), []).append(p)

    now = datetime.now().isoformat()
    stats = {"updated": 0, "created": 0, "nochange": 0, "skipped_dup": 0}

    for nickname, wechat, remark in excel_rows:
        # 1) 微信号匹配
        target = None
        if wechat:
            cands = by_wechat.get(wechat.strip().lower())
            if cands:
                target = cands[0]
        # 2) 昵称匹配
        if target is None and nickname:
            cands = by_name.get(nickname.strip().lower())
            if cands:
                target = cands[0]
        # 3) 备注匹配
        if target is None and remark:
            cands = by_remark.get(remark.strip().lower())
            if cands:
                target = cands[0]

        if target is not None:
            new_wechat = wechat if wechat else target['wechat']
            new_remark = remark if remark else target['wechat_remark']
            if target['wechat'] == new_wechat and target['wechat_remark'] == new_remark:
                stats["nochange"] += 1
            else:
                db.execute(
                    "UPDATE players SET wechat=?, wechat_remark=?, updated_at=? WHERE id=?",
                    [new_wechat, new_remark, now, target['id']],
                )
                stats["updated"] += 1
            continue

        # 匹配不到 -> 新建玩家
        name = nickname or remark or wechat
        db.execute(
            "INSERT INTO players (name, wechat, wechat_remark, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [name, wechat, remark, now, now],
        )
        stats["created"] += 1

    db.commit()

    total = db.execute("SELECT COUNT(*) c FROM players").fetchone()['c']
    empty_wc = db.execute(
        "SELECT COUNT(*) c FROM players WHERE wechat IS NULL OR wechat=''"
    ).fetchone()['c']
    empty_rem = db.execute(
        "SELECT COUNT(*) c FROM players WHERE wechat_remark IS NULL OR wechat_remark=''"
    ).fetchone()['c']

    print(f"\n=== 导入完成 ===")
    print(f"  更新已有玩家: {stats['updated']}")
    print(f"  新建玩家:     {stats['created']}")
    print(f"  无变化:       {stats['nochange']}")
    print(f"\n  系统玩家总数:   {total}")
    print(f"  微信号为空:     {empty_wc}")
    print(f"  微信备注为空:   {empty_rem}")
    db.close()


if __name__ == "__main__":
    main()
