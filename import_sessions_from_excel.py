"""从 Excel 原始组局记录导入为系统 sessions/session_players/payments

说明:
- 以 visit_records 为基准（玩家/日期/桌号已清洗关联）
- 用 Excel 的「真实支付金额」补齐每个 session_player 的 final_fee
- 按 (visit_date, table_number) 去重，source_id = excel_YYYY-MM-DD_{table}
- 每天同类型桌号按出现顺序循环分配物理机器
"""

import os
import sys
import re
import uuid
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from models import get_db
from python_calamine import CalamineWorkbook

EXCEL_PATH = r"C:\Users\zyssh\Desktop\QCOS_青鹭玩家数据库_V2.0 - 副本.xlsx"


def parse_payment(value):
    """把真实支付金额解析为数值; 空/文字记为 0, 同时返回原始文字备注"""
    if value is None:
        return 0.0, None
    s = str(value).strip()
    if not s:
        return 0.0, None
    try:
        # Excel 中可能出现数字或字符串数字
        return float(s), None
    except ValueError:
        # 免单券、走卡、免单券补20 等文字
        return 0.0, s


def load_excel_payments():
    """返回 dict: (date_str, table_no, player_name) -> (amount, raw_remark)"""
    wb = CalamineWorkbook.from_path(EXCEL_PATH)
    ws = wb.get_sheet_by_name('原始组局记录')
    data = ws.to_python()
    cm = {str(h).strip(): i for i, h in enumerate(data[0])}

    payments = {}
    for row in data[1:]:
        if not row or not row[cm['日期']]:
            continue
        d = row[cm['日期']]
        if isinstance(d, datetime):
            d = d.date()
        date_str = str(d)
        try:
            table_no = int(row[cm['牌桌序号']])
        except (ValueError, TypeError):
            continue
        player_name = str(row[cm['标准昵称']]).strip() if row[cm['标准昵称']] else ''
        if not player_name:
            continue
        raw_pay = row[cm['真实支付金额']]
        amount, remark = parse_payment(raw_pay)
        payments[(date_str, table_no, player_name)] = (amount, remark)
    return payments


def assign_machine_id(machine_type, day_counter):
    """同一天同类型按出现顺序循环分配机器"""
    key = machine_type
    idx = day_counter[key]
    day_counter[key] += 1
    if machine_type == '8port':
        return [1, 2][idx % 2]
    return [3, 4][idx % 2]


def import_sessions(dry_run=False):
    db = get_db()
    excel_payments = load_excel_payments()

    # 读取所有 visit_records，按 (date, table_number) 分组
    rows = db.execute(
        """SELECT id, player_id, player_name, visit_date, machine_type, game_type,
                  brought_guest, organizer_name, is_overnight, table_number,
                  is_table_head, table_head_organizer
           FROM visit_records
           ORDER BY visit_date, table_number, id"""
    ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        groups[(r['visit_date'], r['table_number'])].append(r)

    # 为了循环分配机器，先按日期/桌号全局排序
    sorted_keys = sorted(groups.keys())

    stats = {
        'sessions_skipped': 0,
        'sessions_created': 0,
        'session_players_created': 0,
        'payments_created': 0,
        'total_amount': 0.0,
        'unmatched_rows': 0,
    }

    now = datetime.now().isoformat(timespec='seconds')

    for (visit_date, table_no) in sorted_keys:
        source_id = f"excel_{visit_date}_{table_no}"

        # 去重: 已存在 source_id 则跳过
        existing = db.execute(
            'SELECT id FROM sessions WHERE source_id=?', [source_id]
        ).fetchone()
        if existing:
            stats['sessions_skipped'] += 1
            continue

        group_rows = groups[(visit_date, table_no)]
        if not group_rows:
            continue

        # 机器类型与分配
        mt = group_rows[0]['machine_type']
        if mt in ('8', '8port'):
            machine_type = '8port'
        else:
            machine_type = '4port'

        # 按日期计数器循环分配机器
        day_counter = defaultdict(int)
        # 这里需要知道本组在该日期该类型的序号；因为 sorted_keys 已排序，我们按顺序即可
        # 但 day_counter 是局部变量，每次循环重置，会导致总是从 0 开始
        # 改为在函数级维护

    # 由于 day_counter 需要在循环间保持，重写循环
    pass


# 把主逻辑直接写在下面，避免上面的占位 stub

def main(dry_run=False):
    db = get_db()

    # 导入前先清理 8/5、8/6 的测试/垃圾 sessions（之前测试脚本事故遗留）
    if not dry_run:
        test_sessions = db.execute(
            """SELECT id FROM sessions
               WHERE status != 'active' AND source_id IS NULL
                 AND (start_time LIKE '2026-08-05%' OR start_time LIKE '2026-08-06%')"""
        ).fetchall()
        for s in test_sessions:
            sid = s['id']
            db.execute('DELETE FROM session_players WHERE session_id=?', [sid])
            db.execute('DELETE FROM product_sales WHERE session_id=?', [sid])
            db.execute('UPDATE discounts SET used_session_id=NULL WHERE used_session_id=?', [sid])
            db.execute('UPDATE visit_records SET session_id=NULL WHERE session_id=?', [sid])
            db.execute('DELETE FROM sessions WHERE id=?', [sid])
        db.commit()
        if test_sessions:
            print(f"已清理 {len(test_sessions)} 个 8/5-8/6 测试遗留 sessions")

    excel_payments = load_excel_payments()

    rows = db.execute(
        """SELECT id, player_id, player_name, visit_date, machine_type, game_type,
                  brought_guest, organizer_name, is_overnight, table_number,
                  is_table_head, table_head_organizer
           FROM visit_records
           ORDER BY visit_date, table_number, id"""
    ).fetchall()

    groups = defaultdict(list)
    for r in rows:
        if not r['visit_date'] or r['table_number'] is None:
            continue
        groups[(r['visit_date'], int(r['table_number']))].append(r)

    sorted_keys = sorted(groups.keys())

    stats = {
        'sessions_skipped': 0,
        'sessions_created': 0,
        'session_players_created': 0,
        'payments_created': 0,
        'total_amount': 0.0,
        'unmatched_rows': 0,
    }

    now = datetime.now().isoformat(timespec='seconds')
    day_counter = defaultdict(int)

    for (visit_date, table_no) in sorted_keys:
        source_id = f"excel_{visit_date}_{table_no}"

        existing = db.execute(
            'SELECT id FROM sessions WHERE source_id=?', [source_id]
        ).fetchone()
        if existing:
            stats['sessions_skipped'] += 1
            continue

        group_rows = groups[(visit_date, table_no)]
        # 取 machine_type 众数（避免 Excel 里同一桌个别行填错）
        from collections import Counter
        mt_counts = Counter(
            '8port' if str(r['machine_type']) in ('8', '8port') else '4port'
            for r in group_rows
        )
        machine_type = mt_counts.most_common(1)[0][0]

        # 按 (日期, 机器类型) 循环分配机器
        counter_key = (visit_date, machine_type)
        idx = day_counter[counter_key]
        day_counter[counter_key] += 1
        if machine_type == '8port':
            machine_id = [1, 2][idx % 2]
        else:
            machine_id = [3, 4][idx % 2]

        # 时间: 默认当天 12:00 ~ 23:59，通宵则到次日 08:00
        is_overnight = any(r['is_overnight'] for r in group_rows)
        start_dt = datetime.fromisoformat(f"{visit_date}T12:00:00")
        if is_overnight:
            end_dt = start_dt + timedelta(hours=20)  # 12:00 -> 次日 08:00
        else:
            end_dt = start_dt + timedelta(hours=11, minutes=59)
        start_time = start_dt.isoformat()
        end_time = end_dt.isoformat()
        duration_minutes = int((end_dt - start_dt).total_seconds() / 60)

        # 收集支付金额
        player_payments = []
        for r in group_rows:
            key = (visit_date, table_no, r['player_name'])
            if key not in excel_payments:
                stats['unmatched_rows'] += 1
                amount, remark = 0.0, '未匹配到Excel支付金额'
            else:
                amount, remark = excel_payments[key]
            player_payments.append({
                'row': r,
                'amount': amount,
                'remark': remark,
            })

        total_fee = round(sum(p['amount'] for p in player_payments), 2)

        # 桌首/组织者信息写入 note
        heads = [p for p in player_payments if p['row']['is_table_head']]
        head_info = ""
        if heads:
            head_name = heads[0]['row']['player_name']
            head_org = heads[0]['row']['table_head_organizer'] or "无"
            head_info = f"桌首:{head_name}; 桌首组织者:{head_org}; "
        orgs = list(set(p['row']['organizer_name'] for p in player_payments if p['row']['organizer_name']))
        if orgs:
            head_info += f"组织者:{','.join(orgs)}"

        note = f"从Excel原始组局记录导入。{head_info}".strip()

        payment_method = 'cash' if total_fee > 0 else 'unknown'

        # 创建 session
        session_id = None
        if not dry_run:
            cur = db.execute(
                """INSERT INTO sessions (
                    machine_id, start_time, end_time, duration_minutes,
                    fee, final_fee, payment_method, status, note, source_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    machine_id, start_time, end_time, duration_minutes,
                    total_fee, total_fee, payment_method, 'completed', note, source_id
                ]
            )
            session_id = cur.lastrowid

        # 遍历玩家：dry-run 也统计 payments，但跳过写入
        for p in player_payments:
            r = p['row']
            if p['amount'] > 0:
                stats['payments_created'] += 1
                stats['total_amount'] += p['amount']

            if not dry_run:
                sp_cur = db.execute(
                    """INSERT INTO session_players (
                        session_id, player_name, player_id, is_organizer,
                        visit_type, is_overnight, start_time, end_time,
                        duration_minutes, fee, final_fee, discount_amount,
                        product_total, grand_total, payment_method, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    [
                        session_id, r['player_name'], r['player_id'],
                        1 if r['player_id'] and r['player_name'] == r['organizer_name'] else 0,
                        'active', r['is_overnight'],
                        start_time, end_time, duration_minutes,
                        0.0, p['amount'], 0.0, 0.0, p['amount'],
                        'cash' if p['amount'] > 0 else 'unknown',
                        'completed'
                    ]
                )
                sp_id = sp_cur.lastrowid

                # 更新 visit_records 的 session_id 与 payment_amount
                db.execute(
                    "UPDATE visit_records SET session_id=?, payment_amount=? WHERE id=?",
                    [session_id, p['amount'], r['id']]
                )

                # 如有实际支付，写入 payments 流水
                if p['amount'] > 0:
                    out_trade_no = f"IMP-{visit_date.replace('-','')}-{table_no}-{r['player_id'] or 0}-{uuid.uuid4().hex[:6]}"
                    db.execute(
                        """INSERT INTO payments (
                            out_trade_no, method, amount, status, provider,
                            session_player_id, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        [
                            out_trade_no, 'cash', p['amount'], 'SUCCESS', 'manual',
                            sp_id, now, now
                        ]
                    )

        if not dry_run:
            db.commit()

        stats['sessions_created'] += 1
        stats['session_players_created'] += len(player_payments)

        if stats['sessions_created'] % 50 == 0 and not dry_run:
            print(f"  ... 已创建 {stats['sessions_created']} 个 sessions")

    return stats


if __name__ == '__main__':
    dry = '--dry-run' in sys.argv
    mode = "(DRY RUN, 不写入)" if dry else "(正式写入)"
    print(f"开始导入 Excel 组局记录到 sessions {mode}")
    stats = main(dry_run=dry)
    print("\n导入完成:")
    print(f"  sessions 已存在(跳过): {stats['sessions_skipped']}")
    print(f"  sessions 新建: {stats['sessions_created']}")
    print(f"  session_players 新建: {stats['session_players_created']}")
    print(f"  payments 新建: {stats['payments_created']}")
    print(f"  实际支付总金额: {round(stats['total_amount'], 2)}")
    if stats['unmatched_rows']:
        print(f"  ⚠️ 未匹配到 Excel 支付的行: {stats['unmatched_rows']}")
