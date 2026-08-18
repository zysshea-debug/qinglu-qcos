"""青鹭QCOS 运营大脑 - 分析与任务生成 (V1.1 Operation Intelligence Layer)

本模块为纯新增逻辑，不改动任何已有收银 / 计费 / 会员 / CRM 核心代码。
所有计算均基于已有数据（players / sessions / session_players / visit_records /
product_sales / settings），不依赖任何人工输入。

对外暴露函数（供 app.py 注册路由调用）：
    compute_player_scores(db)      -> 重算所有玩家价值评分，写回 players 表
    generate_operation_tasks(db)   -> 生成今日运营任务
    get_operations_dashboard(db)   -> 运营驾驶舱数据
    get_staff_dashboard(db)        -> 店员工作台数据
    complete_task(db, task_id, op) -> 完成任务
    get_gmv_summary(db)            -> 月/日 GMV 与目标
"""

from datetime import datetime, date, timedelta
import calendar

# ===================== 常量 =====================

LEVEL_LABELS = {
    'A+': '核心生态玩家',
    'A': '重点维护',
    'B': '普通活跃',
    'C': '低频玩家',
    'D': '流失/风险',
}

TASK_TYPE_LABELS = {
    'recover_customer': '客户召回',
    'maintain_customer': '客户维护',
    'new_customer_follow': '新客转化',
    'organizer_develop': '组织者培养',
    'risk_warning': '风险预警',
    'table_match': '组局匹配',
    'feedback_review': '反馈复盘',
}

# 风险标签关键词 -> 扣分（单次最多扣 40）
RISK_KEYWORDS = {
    '欠账': 15, '欠款': 15, '赊账': 15,
    '放鸽子': 10, '鸽': 8,
    '投诉': 10,
    '情绪': 8, '冲突': 8,
    '风险': 5,
}


# ===================== 工具 =====================

def _parse_dt(s):
    if not s:
        return None
    s = str(s).strip()
    if 'T' in s:
        try:
            return datetime.fromisoformat(s)
        except ValueError:
            pass
    try:
        return datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        return None


def _today():
    return date.today()


def _month_end(d):
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _get_target(db):
    r = db.execute("SELECT value FROM settings WHERE key='monthly_target_gmv'").fetchone()
    if r:
        try:
            return float(r['value'])
        except (ValueError, TypeError):
            return 30000.0
    return 30000.0


def _gmv_in_range(db, start, end):
    """区间内 GMV = 台费实收(session_players.grand_total) + 商品销售(product_sales)。
    口径与 /api/daily 保持一致（不含会员充值）。"""
    sessions = db.execute(
        '''SELECT id FROM sessions WHERE status='closed'
           AND date(start_time) >= ? AND date(start_time) <= ?''',
        [start, end]
    ).fetchall()
    sids = [s['id'] for s in sessions]
    fee_total = 0.0
    if sids:
        sp = db.execute(
            'SELECT SUM(grand_total) t, SUM(final_fee) f FROM session_players WHERE session_id IN (%s)'
            % ','.join('?' * len(sids)),
            sids
        ).fetchone()
        fee_total = float(sp['t'] or sp['f'] or 0)
    prod = db.execute(
        'SELECT SUM(total) t FROM product_sales WHERE date(created_at) >= ? AND date(created_at) <= ?',
        [start, end]
    ).fetchone()['t'] or 0
    return round(float(fee_total) + float(prod), 2)


# ===================== 玩家价值评分模型 =====================

def _get_activity(db, pid, pname):
    """聚合单个玩家的到店时间、带客、桌头、组织者、消费额。"""
    pname = pname or ''
    dates = []
    rows = db.execute(
        '''SELECT s.start_time FROM sessions s
           JOIN session_players sp ON sp.session_id = s.id
           WHERE s.status = 'closed' AND (sp.player_id = ? OR sp.player_name = ?)''',
        [pid, pname]
    ).fetchall()
    for r in rows:
        dt = _parse_dt(r['start_time'])
        if dt:
            dates.append(dt)
    vr = db.execute(
        'SELECT visit_date FROM visit_records WHERE player_id = ? OR player_name = ?',
        [pid, pname]
    ).fetchall()
    for r in vr:
        dt = _parse_dt(r['visit_date'])
        if dt:
            dates.append(dt)

    brought = db.execute(
        'SELECT COUNT(*) c FROM visit_records WHERE (player_id = ? OR player_name = ?) AND brought_guest = 1',
        [pid, pname]
    ).fetchone()['c'] or 0
    table_head = db.execute(
        'SELECT COUNT(*) c FROM visit_records WHERE (player_id = ? OR player_name = ?) AND is_table_head = 1',
        [pid, pname]
    ).fetchone()['c'] or 0
    organizer = db.execute(
        '''SELECT COUNT(*) c FROM session_players sp JOIN sessions s ON sp.session_id = s.id
           WHERE (sp.player_id = ? OR sp.player_name = ?) AND sp.is_organizer = 1''',
        [pid, pname]
    ).fetchone()['c'] or 0
    sp_fee = db.execute(
        '''SELECT SUM(sp.grand_total) total FROM session_players sp
           JOIN sessions s ON sp.session_id = s.id
           WHERE (sp.player_id = ? OR sp.player_name = ?)''',
        [pid, pname]
    ).fetchone()['total'] or 0
    prod = db.execute(
        '''SELECT SUM(ps.total) total FROM product_sales ps
           JOIN session_players sp ON ps.session_player_id = sp.id
           JOIN sessions s ON sp.session_id = s.id
           WHERE (sp.player_id = ? OR sp.player_name = ?)''',
        [pid, pname]
    ).fetchone()['total'] or 0
    return {
        'dates': dates,
        'brought_guest': int(brought),
        'table_head': int(table_head),
        'organizer': int(organizer),
        'spend': float(sp_fee) + float(prod),
    }


def _score_player(p, act, today):
    pid = p['id']
    visits_30 = sum(1 for d in act['dates'] if 0 <= (today - d.date()).days <= 30)
    total_visits = len(act['dates'])
    spend = act['spend']

    # 消费价值 40分：到店次数(20) + 消费额(20)
    if visits_30 >= 5:
        v_score = 20
    elif visits_30 >= 4:
        v_score = 16
    elif visits_30 >= 3:
        v_score = 12
    elif visits_30 >= 2:
        v_score = 8
    elif visits_30 >= 1:
        v_score = 4
    else:
        v_score = 0
    if spend >= 3000:
        s_score = 20
    elif spend >= 1500:
        s_score = 15
    elif spend >= 500:
        s_score = 10
    elif spend >= 100:
        s_score = 5
    else:
        s_score = 0
    consume_score = v_score + s_score

    # 活跃价值 20分：就近度(10) + 频率(10)
    last = max(act['dates']).date() if act['dates'] else None
    recency_days = (today - last).days if last else 999
    if recency_days <= 7:
        rec = 10
    elif recency_days <= 14:
        rec = 7
    elif recency_days <= 30:
        rec = 4
    else:
        rec = 0
    if visits_30 >= 5:
        freq = 10
    elif visits_30 >= 3:
        freq = 7
    elif visits_30 >= 1:
        freq = 4
    else:
        freq = 0
    active_score = rec + freq

    # 社交价值 30分：带客(15) + 组织者(10) + 桌头(5)
    if act['brought_guest'] >= 3:
        bg = 15
    elif act['brought_guest'] >= 2:
        bg = 10
    elif act['brought_guest'] >= 1:
        bg = 5
    else:
        bg = 0
    org = 10 if (act['organizer'] >= 1 or p.get('is_organizer') == 1) else 0
    th = 5 if act['table_head'] >= 1 else 0
    social_score = min(30, bg + org + th)

    # 风险扣分
    risk_tags = ' '.join([str(p.get('risk_tags') or ''), str(p.get('marketing_tags') or '')])
    penalty = 0
    for kw, val in RISK_KEYWORDS.items():
        if kw in risk_tags:
            penalty += val
    penalty = min(penalty, 40)

    total = max(0, min(100, consume_score + active_score + social_score - penalty))

    if total >= 85:
        level = 'A+'
    elif total >= 70:
        level = 'A'
    elif total >= 50:
        level = 'B'
    elif total >= 30:
        level = 'C'
    else:
        level = 'D'

    return {
        'player_id': pid,
        'name': p.get('name'),
        'customer_score': round(total, 1),
        'customer_level': level,
        'level_label': LEVEL_LABELS[level],
        'consume_score': consume_score,
        'active_score': active_score,
        'social_score': social_score,
        'risk_penalty': penalty,
        'visits_30d': visits_30,
        'total_visits': total_visits,
        'brought_guest': act['brought_guest'],
        'organizer': act['organizer'],
        'spend': round(spend, 2),
        'last_visit': last.isoformat() if last else None,
    }


def compute_player_scores(db):
    """重算所有玩家评分，写回 players 表，返回结果列表。"""
    players = db.execute('SELECT * FROM players').fetchall()
    today = _today()
    results = []
    for p in players:
        p = dict(p)
        act = _get_activity(db, p['id'], p.get('name'))
        sc = _score_player(p, act, today)
        db.execute(
            'UPDATE players SET customer_score=?, customer_level=?, customer_score_updated=? WHERE id=?',
            [sc['customer_score'], sc['customer_level'], datetime.now().isoformat(), p['id']]
        )
        results.append(sc)
    db.commit()
    return results


# ===================== 运营任务生成 =====================

def _refresh_pending(db, pid, task_type):
    """生成前判断是否可生成（实现每日刷新）：
    今日已有 pending -> 'skip'（不重复生成）；
    存在旧的 pending -> 先置为 ignored，再允许重新生成；
    无任何 pending -> 'ok'（允许生成）。"""
    rows = db.execute(
        "SELECT id, created_at FROM operation_tasks WHERE player_id=? AND task_type=? AND status='pending'",
        [pid, task_type]
    ).fetchall()
    today = _today().isoformat()
    for r in rows:
        if (r['created_at'] or '')[:10] == today:
            return 'skip'
        db.execute("UPDATE operation_tasks SET status='ignored' WHERE id=?", [r['id']])
    return 'ok'


def generate_operation_tasks(db):
    today = _today()
    d7 = today - timedelta(days=7)
    d14 = today - timedelta(days=14)
    players = db.execute('SELECT * FROM players').fetchall()
    created = {k: 0 for k in TASK_TYPE_LABELS}
    skipped = 0

    for p in players:
        p = dict(p)
        pid = p['id']
        pname = p.get('name') or ''
        act = _get_activity(db, pid, pname)
        dates = act['dates']
        last = max(dates).date() if dates else None
        visits_30 = sum(1 for d in dates if 0 <= (today - d.date()).days <= 30)
        total_visits = len(dates)
        level = p.get('customer_level') or 'C'

        # 1. 客户召回
        if level in ('A+', 'A'):
            if last is None or last < d7:
                st = _refresh_pending(db, pid, 'recover_customer')
                if st == 'skip':
                    skipped += 1
                else:
                    desc = (f"【A级召回】最近到店：{last or '无记录'}，30天到店 {visits_30} 次。"
                            f"建议微信/电话问候，推送专属优惠唤醒。")
                    db.execute(
                        'INSERT INTO operation_tasks (player_id,task_type,priority,description,status,created_at) '
                        'VALUES (?,?,?,?,?,?)',
                        [pid, 'recover_customer', 'high', desc, 'pending', datetime.now().isoformat()])
                    created['recover_customer'] += 1
        elif level == 'B':
            if last is None or last < d14:
                st = _refresh_pending(db, pid, 'recover_customer')
                if st == 'skip':
                    skipped += 1
                else:
                    desc = (f"【B级召回】最近到店：{last or '无记录'}，已超过14天未到。"
                            f"建议轻量触达提醒。")
                    db.execute(
                        'INSERT INTO operation_tasks (player_id,task_type,priority,description,status,created_at) '
                        'VALUES (?,?,?,?,?,?)',
                        [pid, 'recover_customer', 'medium', desc, 'pending', datetime.now().isoformat()])
                    created['recover_customer'] += 1

        # 2. 客户维护：30天≥5次且近7天未现
        if visits_30 >= 5 and (last is None or last < d7):
            st = _refresh_pending(db, pid, 'maintain_customer')
            if st == 'skip':
                skipped += 1
            else:
                desc = (f"【重点维护】近30天到店 {visits_30} 次，但近7天未出现，优先提醒维系客情。")
                db.execute(
                    'INSERT INTO operation_tasks (player_id,task_type,priority,description,status,created_at) '
                    'VALUES (?,?,?,?,?,?)',
                    [pid, 'maintain_customer', 'high', desc, 'pending', datetime.now().isoformat()])
                created['maintain_customer'] += 1

        # 3. 新客转化：累计到店 1-3 次
        if 1 <= total_visits <= 3:
            st = _refresh_pending(db, pid, 'new_customer_follow')
            if st == 'skip':
                skipped += 1
            else:
                desc = (f"【新客转化】累计到店 {total_visits} 次（新客）。继续维护，引导复购与拉新。")
                db.execute(
                    'INSERT INTO operation_tasks (player_id,task_type,priority,description,status,created_at) '
                    'VALUES (?,?,?,?,?,?)',
                    [pid, 'new_customer_follow', 'normal', desc, 'pending', datetime.now().isoformat()])
                created['new_customer_follow'] += 1

        # 4. 组织者培养：带客≥3 或 已标记组织者
        if act['brought_guest'] >= 3 or p.get('is_organizer') == 1 or act['organizer'] >= 1:
            st = _refresh_pending(db, pid, 'organizer_develop')
            if st == 'skip':
                skipped += 1
            else:
                reason = '带客≥3次' if act['brought_guest'] >= 3 else ('已标记组织者' if p.get('is_organizer') == 1 else '有组局发起记录')
                desc = (f"【常务培养】{reason}。可发展为常务/桌头，给予专属权益与信任。")
                db.execute(
                    'INSERT INTO operation_tasks (player_id,task_type,priority,description,status,created_at) '
                    'VALUES (?,?,?,?,?,?)',
                    [pid, 'organizer_develop', 'normal', desc, 'pending', datetime.now().isoformat()])
                created['organizer_develop'] += 1

        # 5. 风险预警：风险标签命中
        risk_tags = p.get('risk_tags') or ''
        if any(kw in risk_tags for kw in ['欠账', '欠款', '赊账', '放鸽子', '鸽', '投诉', '情绪', '冲突', '风险']):
            st = _refresh_pending(db, pid, 'risk_warning')
            if st == 'skip':
                skipped += 1
            else:
                desc = (f"【风险预警】风险标签：{risk_tags}。需关注客情，避免纠纷与损失。")
                db.execute(
                    'INSERT INTO operation_tasks (player_id,task_type,priority,description,status,created_at) '
                    'VALUES (?,?,?,?,?,?)',
                    [pid, 'risk_warning', 'high', desc, 'pending', datetime.now().isoformat()])
                created['risk_warning'] += 1

    db.commit()
    # V1.3：联动生成反馈复盘任务
    try:
        fb = _generate_feedback_review_tasks(db)
        created['feedback_review'] = fb.get('created', 0)
        return {'created': created, 'skipped_existing': skipped, 'total': sum(created.values())}
    except Exception:
        return {'created': created, 'skipped_existing': skipped, 'total': sum(created.values())}


# ===== V1.3 联动：反馈复盘任务（懒加载避免循环依赖）=====
def _generate_feedback_review_tasks(db):
    from operation_feedback import generate_feedback_review_tasks
    return generate_feedback_review_tasks(db)


# ===================== GMV 与目标 =====================

def get_gmv_summary(db):
    today = _today()
    month_start = today.replace(day=1)
    target = _get_target(db)
    month_gmv = _gmv_in_range(db, month_start.isoformat(), today.isoformat())
    today_gmv = _gmv_in_range(db, today.isoformat(), today.isoformat())
    remain_days = (_month_end(today) - today).days + 1
    remaining = max(0, target - month_gmv)
    pct = round(month_gmv / target * 100, 1) if target else 0
    d30 = today - timedelta(days=30)
    last30 = _gmv_in_range(db, d30.isoformat(), today.isoformat())
    avg_daily = last30 / 30 if last30 else 0
    forecast = round(avg_daily * remain_days, 2)
    return {
        'target': target,
        'month_gmv': month_gmv,
        'today_gmv': today_gmv,
        'remaining': round(remaining, 2),
        'completion_pct': pct,
        'remain_days': remain_days,
        'avg_daily_30d': round(avg_daily, 2),
        'forecast_month_end': forecast,
        'forecast_gap': round(target - forecast, 2),
    }


# ===================== 今日经营概览 =====================

def get_today_overview(db):
    today = _today()
    t_str = today.isoformat()
    tables_closed = db.execute(
        "SELECT COUNT(*) c FROM sessions WHERE date(start_time)=? AND status='closed'", [t_str]
    ).fetchone()['c']
    tables_active = db.execute(
        "SELECT COUNT(*) c FROM sessions WHERE date(start_time)=? AND status='active'", [t_str]
    ).fetchone()['c']
    players_today = db.execute(
        '''SELECT DISTINCT sp.player_id, sp.player_name FROM session_players sp
           JOIN sessions s ON sp.session_id=s.id WHERE date(s.start_time)=?''', [t_str]
    ).fetchall()
    active_players = len(players_today)

    new_count = 0
    repeat_count = 0
    for pt in players_today:
        cnt = db.execute(
            '''SELECT COUNT(*) c FROM session_players sp JOIN sessions s ON sp.session_id=s.id
               WHERE (sp.player_id=? OR sp.player_name=?) AND date(s.start_time) <= ?''',
            [pt['player_id'], pt['player_name'], t_str]
        ).fetchone()['c']
        if cnt <= 1:
            new_count += 1
        else:
            repeat_count += 1

    return {
        'today_gmv': get_gmv_summary(db)['today_gmv'],
        'tables_closed': tables_closed,
        'tables_active': tables_active,
        'active_players': active_players,
        'new_customers': new_count,
        'repeat_customers': repeat_count,
    }


# ===================== 桌局预测 =====================

def get_table_forecast(db):
    today = _today()
    t_str = today.isoformat()
    confirmed = db.execute(
        "SELECT COUNT(*) c FROM sessions WHERE date(start_time)=? AND status='active'", [t_str]
    ).fetchone()['c']
    weekday = today.weekday()
    hist = []
    for w in range(1, 5):
        d = today - timedelta(weeks=w)
        if d.weekday() == weekday:
            c = db.execute(
                "SELECT COUNT(*) c FROM sessions WHERE date(start_time)=? AND status='closed'",
                [d.isoformat()]
            ).fetchone()['c']
            hist.append(c)
    potential = round(sum(hist) / len(hist)) if hist else confirmed
    in_seat = db.execute(
        '''SELECT COUNT(*) c FROM session_players sp JOIN sessions s ON sp.session_id=s.id
           WHERE date(s.start_time)=? AND s.status='active' ''', [t_str]
    ).fetchone()['c']
    avg_seat = 4
    gap_people = max(0, potential * avg_seat - in_seat)
    return {
        'confirmed': confirmed,
        'potential': potential,
        'history_same_weekday': hist,
        'in_seat': in_seat,
        'gap_people': gap_people,
    }


# ===================== 任务查询 =====================

def get_pending_tasks(db):
    rows = db.execute(
        '''SELECT t.*, p.name as player_name, p.customer_level, p.phone
           FROM operation_tasks t LEFT JOIN players p ON t.player_id=p.id
           WHERE t.status='pending'
           ORDER BY CASE t.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, t.created_at'''
    ).fetchall()
    out = {k: [] for k in TASK_TYPE_LABELS}
    for r in rows:
        r = dict(r)
        if r['task_type'] in out:
            out[r['task_type']].append(r)
    return out


def complete_task(db, task_id, operator):
    r = db.execute('SELECT * FROM operation_tasks WHERE id=?', [task_id]).fetchone()
    if not r:
        return False
    db.execute(
        "UPDATE operation_tasks SET status='completed', completed_at=?, operator=? WHERE id=?",
        [datetime.now().isoformat(), operator, task_id]
    )
    db.commit()
    return True


# ===================== 聚合接口 =====================

def get_operations_dashboard(db):
    return {
        'overview': get_today_overview(db),
        'gmv': get_gmv_summary(db),
        'forecast': get_table_forecast(db),
        'tasks': get_pending_tasks(db),
        'level_labels': LEVEL_LABELS,
        'task_type_labels': TASK_TYPE_LABELS,
    }


def get_staff_dashboard(db):
    today = _today()
    tasks = get_pending_tasks(db)
    flat = []
    for k, v in tasks.items():
        for t in v:
            flat.append(t)
    key = db.execute(
        "SELECT id,name,customer_level,customer_score,phone,last_visit FROM players "
        "WHERE customer_level IN ('A+','A') ORDER BY customer_score DESC"
    ).fetchall()
    month = today.month
    birthdays = []
    allp = db.execute(
        "SELECT id,name,birthday,phone FROM players WHERE birthday IS NOT NULL AND birthday != ''"
    ).fetchall()
    for p in allp:
        b = p['birthday']
        if b and len(b) >= 5 and int(str(b)[5:7]) == month:
            birthdays.append(dict(p))
    return {
        'tasks': flat,
        'key_customers': [dict(k) for k in key],
        'birthdays': birthdays,
        'forecast': get_table_forecast(db),
    }
