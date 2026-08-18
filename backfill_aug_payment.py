"""回填 2026年8月 组局「真实支付金额」到 visit_records.payment_amount

匹配策略：visit_records 由同一份 Excel 原始组局记录 1:1 生成，
用复合键 (visit_date, player_name, machine_type, is_overnight, table_number, is_table_head)
精确匹配，避免只按 (日期+姓名) 造成的 9 组歧义。

仅处理 2026-08 的数据，不重导玩家、不触碰其它月份。
"""
import os
import sys
import shutil
import sqlite3
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DB_PATH
from python_calamine import CalamineWorkbook

EXCEL_PATH = r"C:\Users\zyssh\Desktop\QCOS_青鹭玩家数据库_V2.0 - 副本.xlsx"
TARGET_YEAR, TARGET_MONTH = 2026, 8


def str_val(v):
    if v is None:
        return ""
    if isinstance(v, float) and v == int(v):
        return str(int(v))
    return str(v).strip()


def norm_machine(v):
    s = str_val(v)
    try:
        return str(int(float(s)))
    except (ValueError, TypeError):
        return s


def norm_int_flag(v):
    s = str_val(v)
    return 1 if s in ('1', '是', 'true', 'True', 1, 1.0) else 0


def norm_table_num(v):
    s = str_val(v)
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def build_key(visit_date, name, machine, overnight, table_num, head):
    return (visit_date, (name or '').strip(), norm_machine(machine),
            norm_int_flag(overnight), norm_table_num(table_num), norm_int_flag(head))


def main():
    # 备份
    bak = DB_PATH + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copyfile(DB_PATH, bak)
    print(f"已备份数据库: {bak}")

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # 读取 DB 中 8 月 visit_records
    db_rows = db.execute(
        """SELECT id, visit_date, player_name, machine_type, is_overnight,
                  table_number, is_table_head
           FROM visit_records WHERE visit_date LIKE '2026-08%'"""
    ).fetchall()
    db_index = {}
    for r in db_rows:
        key = build_key(r['visit_date'], r['player_name'], r['machine_type'],
                        r['is_overnight'], r['table_number'], r['is_table_head'])
        db_index.setdefault(key, []).append(r['id'])

    # 读取 Excel
    wb = CalamineWorkbook.from_path(EXCEL_PATH)
    ws = wb.get_sheet_by_name('原始组局记录').to_python(skip_empty_area=False)
    header = ws[0]
    col = {}
    for i, h in enumerate(header):
        if h:
            col[str(h).strip()] = i

    def get(r, name):
        idx = col.get(name)
        if idx is None or idx >= len(r):
            return ""
        return str_val(r[idx])

    excel_rows = []
    for r in ws[1:]:
        if not r or not r[0]:
            continue
        d = r[col.get('日期', 0)]
        if not isinstance(d, (datetime.date, datetime.datetime)):
            continue
        if d.year != TARGET_YEAR or d.month != TARGET_MONTH:
            continue
        vdate = d.isoformat()[:10]
        pay_raw = r[col.get('真实支付金额', 13)] if col.get('真实支付金额') is not None and col.get('真实支付金额') < len(r) else ""
        try:
            pay = float(pay_raw)
        except (ValueError, TypeError):
            continue
        if pay <= 0:
            continue
        name = get(r, '标准昵称') or get(r, '姓名')
        key = build_key(vdate, name, get(r, '四/八'), get(r, '是否通宵'),
                        get(r, '牌桌序号'), get(r, '桌首标记'))
        excel_rows.append((key, pay, vdate, name))

    # 匹配统计
    matched = 0
    multi = 0
    unmatched = 0
    unmatched_examples = []
    total_pay = 0.0
    updates = []  # (id, pay)
    for key, pay, vdate, name in excel_rows:
        ids = db_index.get(key, [])
        if len(ids) == 1:
            updates.append((ids[0], pay))
            matched += 1
            total_pay += pay
        elif len(ids) > 1:
            for i in ids:
                updates.append((i, pay))
            multi += 1
            total_pay += pay
        else:
            unmatched += 1
            if len(unmatched_examples) < 15:
                unmatched_examples.append(f"{vdate} {name} ¥{pay}")

    print(f"Excel 8月有支付记录行: {len(excel_rows)}")
    print(f"  精确匹配(1:1): {matched}")
    print(f"  多匹配(>1):   {multi}")
    print(f"  未匹配:        {unmatched}")
    if unmatched_examples:
        print("  未匹配示例:", "; ".join(unmatched_examples))
    print(f"待写入支付总额: ¥{round(total_pay, 2)}")

    # 执行写入
    for rid, pay in updates:
        db.execute("UPDATE visit_records SET payment_amount=? WHERE id=?", [round(pay, 2), rid])
    db.commit()

    # 校验
    sum_db = db.execute(
        "SELECT SUM(payment_amount) t, COUNT(*) c FROM visit_records WHERE visit_date LIKE '2026-08%' AND payment_amount>0"
    ).fetchone()
    print(f"\n写入完成 -> 8月已填支付 visit_records: {sum_db['c']} 条, 合计 ¥{round(sum_db['t'] or 0, 2)}")
    db.close()


if __name__ == "__main__":
    main()
