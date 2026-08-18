"""QCOS Excel 导入脚本 - 从 V2.0 Excel 导入玩家档案+行为数据+组局记录

修正说明 (V2 副本兼容):
- 玩家主键 qcos_id 取自「玩家总表.玩家ID」(P001...)，不再依赖「玩家档案」含玩家ID列
- 玩家档案按「昵称」匹配到总表，用于补全详细资料字段
- visit_records 导入改为幂等：先清空再全量插入，避免重复累积
"""

import sys
import os
import sqlite3
from datetime import datetime, date
import shutil

# 确保能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH
from python_calamine import CalamineWorkbook

EXCEL_PATH = r"C:\Users\zyssh\Desktop\QCOS_青鹭玩家数据库_V2.0 - 副本.xlsx"


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def str_val(v):
    """安全转字符串，处理 float/None/日期"""
    if v is None:
        return ""
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return str(v)
    return str(v).strip()


def import_players(db, wb):
    """导入玩家档案 + 玩家总表行为汇总

    以「玩家总表」为主清单(含 玩家ID)，按「昵称」从「玩家档案」补全资料。
    """
    # === 玩家总表 (行为汇总, 主清单) ===
    wt = wb.get_sheet_by_name("玩家总表")
    dtt = wt.to_python()
    htt = dtt[3]  # Row 4 is header
    cmt = {}
    for i, h in enumerate(htt):
        if h:
            cmt[str(h).strip()] = i

    # 玩家总表 -> summary_by_id 与 qcosid_by_nick
    summary_by_id = {}
    for r in range(4, len(dtt)):
        row = dtt[r]
        if not row or not row[0]:
            continue
        pid = str_val(row[cmt.get("玩家ID", 0)])
        nm = str_val(row[cmt.get("昵称", 1)])
        if not pid or not nm:
            continue
        summary_by_id[pid] = {
            "first_visit": str_val(row[cmt.get("首次到店", 2)])[:10] if cmt.get("首次到店") is not None else "",
            "last_visit": str_val(row[cmt.get("最近到店", 3)])[:10] if cmt.get("最近到店") is not None else "",
            "total_visits": int(row[cmt.get("历史到店次数", 5)]) if cmt.get("历史到店次数") is not None and row[cmt.get("历史到店次数", 5)] else 0,
            "visits_30d": int(row[cmt.get("近30天到店", 6)]) if cmt.get("近30天到店") is not None and row[cmt.get("近30天到店", 6)] else 0,
            "activity_level": str_val(row[cmt.get("活跃度", 8)]) if cmt.get("活跃度") is not None else "",
            "player_type": str_val(row[cmt.get("主要类型", 9)]) if cmt.get("主要类型") is not None else "",
            "common_mode": str_val(row[cmt.get("常玩模式", 10)]) if cmt.get("常玩模式") is not None else "",
            "active_behavior": str_val(row[cmt.get("主动行为分类", 15)]) if cmt.get("主动行为分类") is not None else "",
            "is_organizer": 1 if str_val(row[cmt.get("社区组织者", 16)]) == "是" else 0 if cmt.get("社区组织者") is not None else 0,
            "organizer_level": str_val(row[cmt.get("组织者等级", 17)]) if cmt.get("组织者等级") is not None else "",
            "maintenance_priority": str_val(row[cmt.get("维护优先级", 21)]) if cmt.get("维护优先级") is not None else "",
            "follow_up_status": str_val(row[cmt.get("跟进状态", 23)]) if cmt.get("跟进状态") is not None else "",
            "risk_tags": str_val(row[cmt.get("风险标签", 24)]) if cmt.get("风险标签") is not None else "",
            "marketing_tags": str_val(row[cmt.get("营销标签", 25)]) if cmt.get("营销标签") is not None else "",
            "profile_completeness": str_val(row[cmt.get("资料完整度", 26)]) if cmt.get("资料完整度") is not None else "",
        }

    # === 玩家档案 (详细资料, 按昵称索引) ===
    wa = wb.get_sheet_by_name("玩家档案")
    da = wa.to_python()
    ha = da[3]
    cma = {}
    for i, h in enumerate(ha):
        if h:
            cma[str(h).strip()] = i

    profile_by_nick = {}
    for r in range(4, len(da)):
        row = da[r]
        if not row or not row[0]:
            continue
        nm = str_val(row[cma.get("昵称", 0)])
        if nm:
            profile_by_nick[nm] = row

    # === 遍历玩家总表(主清单)，插入/更新 players 表 ===
    now = datetime.now().isoformat()
    inserted = 0
    updated = 0

    for r in range(4, len(dtt)):
        row = dtt[r]
        if not row or not row[0]:
            continue

        qcos_id = str_val(row[cmt.get("玩家ID", 0)])
        nickname = str_val(row[cmt.get("昵称", 1)])
        if not qcos_id or not nickname:
            continue

        summary = summary_by_id.get(qcos_id, {})
        prow = profile_by_nick.get(nickname)

        def getp(name):
            if prow is None:
                return ""
            idx = cma.get(name)
            if idx is None or idx >= len(prow):
                return ""
            return str_val(prow[idx])

        player_data = {
            "name": nickname,
            "qcos_id": qcos_id,
            "real_name": getp("真实姓名"),
            "preferred_name": getp("希望称呼"),
            "gender": getp("性别"),
            "birthday": getp("生日"),
            "phone": getp("手机号"),
            "wechat": getp("微信号"),
            "wechat_remark": getp("微信备注"),
            "area": getp("常住区域"),
            "occupation": getp("职业"),
            "industry": getp("行业"),
            "source_channel": getp("来源渠道"),
            "introducer": getp("介绍人"),
            "relationship_strength": getp("关系强度"),
            "personality_tags": getp("性格标签"),
            "player_type": summary.get("player_type", "") or getp("玩家类型"),
            "skill_level": getp("K值/水平"),
            "preferred_mode": summary.get("common_mode", "") or getp("偏好玩法"),
            "preferred_time": getp("常来时段"),
            "can_overnight": getp("可否通宵"),
            "tournament_interest": getp("比赛兴趣"),
            "organizer_candidate": getp("组织者候选"),
            "organizer_level": summary.get("organizer_level", ""),
            "organizer_note": getp("组织者备注"),
            "first_visit": summary.get("first_visit", ""),
            "last_visit": summary.get("last_visit", ""),
            "total_visits": summary.get("total_visits", 0),
            "visits_30d": summary.get("visits_30d", 0),
            "activity_level": summary.get("activity_level", ""),
            "common_mode": summary.get("common_mode", ""),
            "active_behavior": summary.get("active_behavior", ""),
            "is_organizer": summary.get("is_organizer", 0),
            "maintenance_priority": summary.get("maintenance_priority", ""),
            "marketing_tags": summary.get("marketing_tags", ""),
            "risk_tags": summary.get("risk_tags", ""),
            "follow_up_status": summary.get("follow_up_status", ""),
            "drink_preference": getp("饮品偏好"),
            "price_sensitivity": getp("价格敏感度"),
            "profile_completeness": summary.get("profile_completeness", ""),
            "notes": getp("重要提醒"),
            "updated_at": now,
        }

        existing = db.execute("SELECT id FROM players WHERE qcos_id=?", [qcos_id]).fetchone()
        if existing:
            set_parts = []
            set_vals = []
            for k, v in player_data.items():
                if k == "qcos_id":
                    continue
                set_parts.append(f"{k}=?")
                set_vals.append(v)
            set_vals.append(existing["id"])
            db.execute(f"UPDATE players SET {', '.join(set_parts)} WHERE id=?", set_vals)
            updated += 1
        else:
            player_data["created_at"] = now
            cols = ", ".join(player_data.keys())
            placeholders = ", ".join(["?"] * len(player_data))
            db.execute(f"INSERT INTO players ({cols}) VALUES ({placeholders})", list(player_data.values()))
            inserted += 1

    db.commit()
    print(f"玩家档案导入完成: 新增 {inserted} 人, 更新 {updated} 人")
    return inserted + updated


def import_visit_records(db, wb):
    """导入原始组局记录 (幂等: 先清空再全量插入)"""
    # 清空历史导入记录, 保证与 Excel 完全一致、不累积重复
    db.execute("DELETE FROM visit_records")
    db.commit()

    ws = wb.get_sheet_by_name("原始组局记录")
    data = ws.to_python()
    headers = data[0]  # Row 1 is header

    col_map = {}
    for i, h in enumerate(headers):
        if h:
            col_map[str(h).strip()] = i

    # 建立 昵称 -> player_id 映射
    players = db.execute("SELECT id, name, qcos_id FROM players").fetchall()
    name_to_id = {}
    for p in players:
        name_to_id[p["name"].strip()] = p["id"]

    now = datetime.now().isoformat()
    inserted = 0
    skipped = 0

    for r in range(1, len(data)):
        row = data[r]
        if not row or not row[0]:
            continue

        def get_col(name):
            idx = col_map.get(name)
            if idx is None or idx >= len(row):
                return ""
            return str_val(row[idx])

        # 日期处理
        raw_date = row[col_map.get("日期", 0)] if col_map.get("日期") is not None and col_map.get("日期") < len(row) else ""
        visit_date = str_val(raw_date)
        if not visit_date:
            skipped += 1
            continue
        if len(visit_date) > 10:
            visit_date = visit_date[:10]
        try:
            if "-" not in visit_date:
                serial = int(float(visit_date))
                visit_date = str(date.fromordinal(date(1899, 12, 30).toordinal() + serial))
        except (ValueError, TypeError):
            skipped += 1
            continue

        nickname = get_col("标准昵称") or get_col("姓名")
        if not nickname:
            skipped += 1
            continue

        player_id = name_to_id.get(nickname.strip())

        machine_type = get_col("四/八")
        game_type = get_col("娱乐/竞技")
        brought_guest = 1 if get_col("是否带人") == "是" else 0
        organizer = get_col("组织者")
        is_overnight = 1 if get_col("是否通宵") == "是" else 0

        table_num = None
        try:
            table_num = int(float(get_col("牌桌序号")))
        except (ValueError, TypeError):
            pass

        is_table_head = 1 if get_col("桌首标记") == "1" else 0
        table_head_org = get_col("桌首组织者")
        data_quality = get_col("数据质量")

        # 真实支付金额（Excel 组局记录“真实支付金额”列）
        pay_raw = get_col("真实支付金额")
        payment_amount = 0.0
        try:
            payment_amount = float(pay_raw)
        except (ValueError, TypeError):
            payment_amount = 0.0

        db.execute("""
            INSERT INTO visit_records
            (player_id, player_name, visit_date, machine_type, game_type,
             brought_guest, organizer_name, is_overnight, table_number,
             is_table_head, table_head_organizer, data_quality, payment_amount, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [player_id, nickname, visit_date, machine_type, game_type,
              brought_guest, organizer, is_overnight, table_num,
              is_table_head, table_head_org, data_quality, payment_amount, now])
        inserted += 1

    db.commit()
    print(f"组局记录导入完成: {inserted} 条, 跳过 {skipped} 条")
    return inserted


def main():
    if not os.path.exists(EXCEL_PATH):
        print(f"错误: 找不到 Excel 文件: {EXCEL_PATH}")
        sys.exit(1)

    print(f"读取 Excel: {EXCEL_PATH}")
    wb = CalamineWorkbook.from_path(EXCEL_PATH)
    print(f"Sheets: {wb.sheet_names}")

    db = get_db()

    # 安全备份当前数据库
    bak = DB_PATH + ".bak_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copyfile(DB_PATH, bak)
    print(f"已备份当前数据库: {bak}")

    print("\n--- 导入玩家档案 ---")
    player_count = import_players(db, wb)

    print("\n--- 导入组局记录 ---")
    record_count = import_visit_records(db, wb)

    # 验证
    total = db.execute("SELECT COUNT(*) as c FROM players").fetchone()["c"]
    visits = db.execute("SELECT COUNT(*) as c FROM visit_records").fetchone()["c"]
    linked = db.execute("SELECT COUNT(*) as c FROM visit_records WHERE player_id IS NOT NULL").fetchone()["c"]
    mx = db.execute("SELECT MAX(visit_date) as m FROM visit_records").fetchone()["m"]

    print(f"\n=== 导入结果 ===")
    print(f"  玩家总数: {total}")
    print(f"  组局记录: {visits} (其中已关联玩家: {linked})")
    print(f"  组局记录最新日期: {mx}")

    dist = db.execute("SELECT activity_level, COUNT(*) as c FROM players GROUP BY activity_level ORDER BY c DESC").fetchall()
    print(f"\n  活跃度分布:")
    for d in dist:
        print(f"    {d['activity_level'] or '(空)'}: {d['c']}")

    org = db.execute("SELECT is_organizer, COUNT(*) as c FROM players GROUP BY is_organizer").fetchall()
    print(f"\n  组织者分布:")
    for o in org:
        print(f"    {'是' if o['is_organizer'] else '否'}: {o['c']}")

    # 疑似异常昵称(纯数字/纯英文, 可能需人工清理)
    susp = db.execute(
        "SELECT name FROM players WHERE name GLOB '*[0-9]*' AND name NOT GLOB '*[一-鿿]*' "
        "OR (name NOT GLOB '*[一-鿿]*' AND length(name)<6 AND name NOT LIKE 'P%')"
    ).fetchall()
    if susp:
        print(f"\n  提示: 以下 {len(susp)} 个昵称疑似测试/异常数据, 建议人工核对:")
        print("    ", ", ".join(s['name'] for s in susp[:20]))

    db.close()
    print("\n导入完成!")


if __name__ == "__main__":
    main()
