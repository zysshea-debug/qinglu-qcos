"""青鹭QCOS V1.3 经营反馈闭环 - 反馈与复盘核心模块
(Operation Feedback Loop & Table Review)

职责（来自任务）：
    桌局反馈保存/读取、玩家体验画像更新、数据回流、组合分析联动、
    运营复盘聚合、店员反馈任务生成、每日经营快照沉淀。

不改动任何已有收银 / 计费 / 会员 / CRM 核心代码；与 V1.1 operations、
V1.2 player_matching 完全兼容（仅新增表与 players 字段）。

对外函数：
    submit_feedback(db, session_id, payload, operator) -> 保存反馈并回流数据
    get_feedback(db, session_id=None, limit)           -> 反馈列表
    recompute_experience(db)                           -> 重算全部玩家体验画像
    get_operation_review(db, date_str)                 -> 指定日期经营复盘
    generate_feedback_review_tasks(db)                 -> 生成反馈复盘运营任务
    generate_daily_snapshot(db, date_str)              -> 生成/更新每日快照
    get_daily_snapshots(db, limit)                     -> 历史快照列表
    CONFLICT_TYPE_LABELS / FEEDBACK_FIELDS
"""

from datetime import datetime, date, timedelta

from operations import TASK_TYPE_LABELS, _refresh_pending, _today, _parse_dt
from table_learning import recompute_pair_stats, CONFLICT_TYPE_LABELS

# 反馈评分字段（1-5）
FEEDBACK_FIELDS = ['atmosphere_score', 'compatibility_score', 'table_quality_score',
                   'conflict_level', 'conflict_type', 'notes']


def _now():
    return datetime.now().isoformat()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ===================== 桌局反馈 =====================

def _get_feedback_rows_for_player(db, pid):
    """取某玩家参与的所有桌局反馈（join session_feedback）。"""
    return db.execute(
        '''SELECT f.* FROM session_feedback f
           JOIN session_players sp ON sp.session_id = f.session_id
           WHERE sp.player_id = ?
           ORDER BY f.created_at''',
        [pid]
    ).fetchall()


def _compute_player_experience(db, pid):
    """基于某人参与的桌局反馈，计算体验画像字段。返回 dict（不写库）。"""
    fbs = _get_feedback_rows_for_player(db, pid)
    total = len(fbs)
    if total == 0:
        return {
            'experience_score': None, 'compatibility_score': None,
            'conflict_count': 0, 'positive_table_count': 0, 'negative_table_count': 0,
            'fb_count': 0,
        }
    sum_avg = 0.0
    sum_compat = 0.0
    positive = 0
    negative = 0
    conflict = 0
    for f in fbs:
        avg = (f['atmosphere_score'] + f['compatibility_score'] + f['table_quality_score']) / 3.0
        sum_avg += avg
        sum_compat += (f['compatibility_score'] or 3)
        cl = f['conflict_level'] or 1
        if avg >= 4 and cl <= 1:
            positive += 1
        if avg <= 2 or cl >= 3:
            negative += 1
        if cl >= 3:
            conflict += 1
    avg_score = sum_avg / total
    pos_ratio = positive / total
    # 体验评分：基础分(avg/5*100) + 正向占比调整 - 冲突惩罚
    base = avg_score / 5 * 100
    conflict_penalty = negative / total * 30
    experience_score = _clamp(round(base + (pos_ratio - 0.5) * 20 - conflict_penalty, 1), 0, 100)
    compatibility_score = _clamp(round(sum_compat / total / 5 * 100, 1), 0, 100)
    return {
        'experience_score': experience_score,
        'compatibility_score': compatibility_score,
        'conflict_count': conflict,
        'positive_table_count': positive,
        'negative_table_count': negative,
        'fb_count': total,
    }


def recompute_experience(db):
    """重算全部玩家体验画像，写回 players 表。返回受影响人数。"""
    players = db.execute('SELECT * FROM players').fetchall()
    cnt = 0
    for p in players:
        pid = p['id']
        exp = _compute_player_experience(db, pid)
        if exp['fb_count'] == 0:
            continue  # 无反馈的玩家保持空集/默认，不写
        db.execute(
            '''UPDATE players
               SET experience_score=?, compatibility_score=?, conflict_count=?,
                   positive_table_count=?, negative_table_count=?
               WHERE id=?''',
            [exp['experience_score'], exp['compatibility_score'], exp['conflict_count'],
             exp['positive_table_count'], exp['negative_table_count'], pid]
        )
        cnt += 1
    db.commit()
    return cnt


def _recompute_experience_for_session(db, session_id):
    """仅回流某桌局玩家（数据闭环用，避免全量重算）。"""
    pids = db.execute(
        'SELECT DISTINCT player_id FROM session_players WHERE session_id=? AND player_id IS NOT NULL',
        [session_id]
    ).fetchall()
    for r in pids:
        pid = r['player_id']
        exp = _compute_player_experience(db, pid)
        db.execute(
            '''UPDATE players
               SET experience_score=?, compatibility_score=?, conflict_count=?,
                   positive_table_count=?, negative_table_count=?
               WHERE id=?''',
            [exp['experience_score'], exp['compatibility_score'], exp['conflict_count'],
             exp['positive_table_count'], exp['negative_table_count'], pid]
        )
    db.commit()


def submit_feedback(db, session_id, payload, operator):
    """保存（upsert）一桌反馈，并回流玩家体验 + 组合统计。返回反馈记录 dict。"""
    # 字段校验与归一
    def _i(v, d=3):
        try:
            v = int(v)
        except (TypeError, ValueError):
            return d
        return _clamp(v, 1, 5)
    atm = _i(payload.get('atmosphere_score'), 3)
    comp = _i(payload.get('compatibility_score'), 3)
    qual = _i(payload.get('table_quality_score'), 3)
    conflict_level = _i(payload.get('conflict_level'), 1)
    conflict_type = payload.get('conflict_type') or 'none'
    if conflict_type not in CONFLICT_TYPE_LABELS:
        conflict_type = 'none'
    notes = (payload.get('notes') or '').strip()

    existing = db.execute('SELECT id FROM session_feedback WHERE session_id=?', [session_id]).fetchone()
    now = _now()
    if existing:
        db.execute(
            '''UPDATE session_feedback
               SET atmosphere_score=?, compatibility_score=?, table_quality_score=?,
                   conflict_level=?, conflict_type=?, notes=?, operator=?, created_at=?
               WHERE session_id=?''',
            [atm, comp, qual, conflict_level, conflict_type, notes, operator, now, session_id]
        )
        fid = existing['id']
    else:
        cur = db.execute(
            '''INSERT INTO session_feedback
               (session_id, atmosphere_score, compatibility_score, table_quality_score,
                conflict_level, conflict_type, notes, operator, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [session_id, atm, comp, qual, conflict_level, conflict_type, notes, operator, now]
        )
        fid = cur.lastrowid
    db.commit()

    # 数据回流：更新该桌玩家体验 + 重算组合统计
    _recompute_experience_for_session(db, session_id)
    recompute_pair_stats(db)
    return db.execute('SELECT * FROM session_feedback WHERE id=?', [fid]).fetchone()


def get_feedback(db, session_id=None, limit=50):
    """反馈列表（含 session 信息 + 玩家姓名）。"""
    if session_id is not None:
        rows = db.execute(
            '''SELECT f.*, s.start_time, s.status AS session_status
               FROM session_feedback f JOIN sessions s ON f.session_id = s.id
               WHERE f.session_id = ?
               ORDER BY f.created_at DESC LIMIT ?''',
            [session_id, limit]
        ).fetchall()
    else:
        rows = db.execute(
            '''SELECT f.*, s.start_time, s.status AS session_status
               FROM session_feedback f JOIN sessions s ON f.session_id = s.id
               ORDER BY f.created_at DESC LIMIT ?''',
            [limit]
        ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        names = db.execute(
            '''SELECT GROUP_CONCAT(DISTINCT p.name) AS names
               FROM session_players sp JOIN players p ON p.id = sp.player_id
               WHERE sp.session_id = ? AND sp.player_id IS NOT NULL''',
            [r['session_id']]
        ).fetchone()
        r['player_names'] = names['names'] if names and names['names'] else ''
        out.append(r)
    return out


# ===================== 运营复盘 =====================

def _gmv_on_date(db, date_str):
    """指定日期 GMV = 台费实收 + 商品（不含充值）。"""
    sids = [s['id'] for s in db.execute(
        "SELECT id FROM sessions WHERE status='closed' AND date(start_time)=?", [date_str]
    ).fetchall()]
    fee_total = 0.0
    if sids:
        sp = db.execute(
            'SELECT SUM(grand_total) t, SUM(final_fee) f FROM session_players WHERE session_id IN (%s)'
            % ','.join('?' * len(sids)), sids
        ).fetchone()
        fee_total = float(sp['t'] or sp['f'] or 0)
    prod = db.execute(
        'SELECT SUM(total) t FROM product_sales WHERE date(created_at)=?', [date_str]
    ).fetchone()['t'] or 0
    return round(float(fee_total) + float(prod), 2)


def _session_avg_of(db, session_id):
    f = db.execute('SELECT * FROM session_feedback WHERE session_id=?', [session_id]).fetchone()
    if not f:
        return None
    return (f['atmosphere_score'] + f['compatibility_score'] + f['table_quality_score']) / 3.0


def get_operation_review(db, date_str=None):
    """指定日期经营复盘聚合。默认昨天。"""
    if not date_str:
        date_str = (_today() - timedelta(days=1)).isoformat()

    gmv = _gmv_on_date(db, date_str)
    sessions = db.execute(
        "SELECT COUNT(*) c FROM sessions WHERE date(start_time)=?", [date_str]
    ).fetchone()['c']
    # 平均桌局质量（当天有反馈的桌）
    fbs = db.execute(
        "SELECT * FROM session_feedback f JOIN sessions s ON f.session_id=s.id WHERE date(s.start_time)=?",
        [date_str]
    ).fetchall()
    if fbs:
        avg_quality = round(sum(_session_avg_of(db, f['session_id']) for f in fbs) / len(fbs), 2)
        conflict_fb = sum(1 for f in fbs if (f['conflict_level'] or 1) >= 3)
    else:
        avg_quality = 0
        conflict_fb = 0

    # 新客：当天首局且历史仅此一次
    new_players = db.execute(
        '''SELECT p.id, p.name FROM players p
           WHERE (SELECT MIN(date(s.start_time)) FROM session_players sp
                  JOIN sessions s ON sp.session_id=s.id WHERE sp.player_id=p.id) = ?''',
        [date_str]
    ).fetchall()
    # 流失客户：近期(>=30天)无到店 或 等级D
    d30 = (_parse_dt(date_str) - timedelta(days=30)).isoformat() if _parse_dt(date_str) else ''
    churned = db.execute(
        '''SELECT id, name, customer_level, last_visit FROM players
           WHERE customer_level = 'D'
              OR (last_visit IS NOT NULL AND last_visit < ?)''',
        [d30]
    ).fetchall()

    # 任务完成率（当天创建的任务）
    total_tasks = db.execute(
        "SELECT COUNT(*) c FROM operation_tasks WHERE date(created_at)=?", [date_str]
    ).fetchone()['c']
    done_tasks = db.execute(
        "SELECT COUNT(*) c FROM operation_tasks WHERE date(created_at)=? AND status='completed'", [date_str]
    ).fetchone()['c']
    completion_rate = round(done_tasks / total_tasks * 100, 1) if total_tasks else 0.0

    from table_learning import get_best_combinations, get_risk_combinations
    best = get_best_combinations(db, 10)
    risk = get_risk_combinations(db, 10)

    return {
        'date': date_str,
        'gmv': gmv,
        'sessions': sessions,
        'feedback_count': len(fbs),
        'average_table_score': avg_quality,
        'conflict_tables': conflict_fb,
        'new_customers': [dict(n) for n in new_players],
        'new_customer_count': len(new_players),
        'churned_customers': [dict(c) for c in churned],
        'churned_count': len(churned),
        'task_total': total_tasks,
        'task_done': done_tasks,
        'task_completion_rate': completion_rate,
        'best_combinations': best,
        'risk_combinations': risk,
        'task_type_labels': TASK_TYPE_LABELS,
    }


# ===================== 反馈复盘任务 =====================

def generate_feedback_review_tasks(db):
    """生成 feedback_review 运营任务（每日刷新）。返回 {created,...}。

    规则：
      1) 风险组合（负向桌局>=2 且近14天有同桌）-> 「组合X与Y连续低评分，需关注」
      2) 新客（首局且历史仅1次）且当次体验正向 -> 「Z首次体验良好，建议二次维护」
    """
    today = _today()
    created = 0
    d14 = (today - timedelta(days=14)).isoformat()

    # 1) 风险组合
    from table_learning import get_risk_combinations
    risks = get_risk_combinations(db, 30)
    for r in risks:
        if r['negative_count'] < 2:
            continue
        # 近14天有同桌才值得关注
        last = db.execute(
            '''SELECT MAX(date(s.start_time)) FROM session_players sp1
               JOIN session_players sp2 ON sp1.session_id=sp2.session_id
               JOIN sessions s ON sp1.session_id=s.id
               WHERE ((sp1.player_id=? AND sp2.player_id=?) OR (sp1.player_id=? AND sp2.player_id=?))''',
            [r['player_a_id'], r['player_b_id'], r['player_b_id'], r['player_a_id']]
        ).fetchone()[0]
        if last and last < d14:
            continue
        st = _refresh_pending(db, r['player_a_id'], 'feedback_review')
        if st != 'ok':
            continue
        desc = (f"【反馈复盘】组合 {r['player_a_name']} 与 {r['player_b_name']} "
                f"历史平均评分 {r['average_score']}、负向桌局 {r['negative_count']} 次"
                f"（{r['reason']}），建议人工评估是否避免同桌。")
        db.execute(
            'INSERT INTO operation_tasks (player_id, task_type, priority, description, status, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [r['player_a_id'], 'feedback_review', 'high', desc, 'pending', _now()]
        )
        created += 1

    # 2) 新客首次体验良好
    newp = db.execute(
        '''SELECT p.id, p.name FROM players p
           WHERE (SELECT COUNT(DISTINCT sp.session_id) FROM session_players sp
                  JOIN sessions s ON sp.session_id=s.id WHERE sp.player_id=p.id) = 1'''
    ).fetchall()
    for p in newp:
        pid = p['id']
        fbs = _get_feedback_rows_for_player(db, pid)
        if not fbs:
            continue
        avg = sum((f['atmosphere_score'] + f['compatibility_score'] + f['table_quality_score']) / 3.0
                  for f in fbs) / len(fbs)
        if avg < 4:
            continue
        st = _refresh_pending(db, pid, 'feedback_review')
        if st != 'ok':
            continue
        desc = (f"【反馈复盘】新客 {p['name']} 首次体验良好（平均评分 {round(avg,1)}），"
                f"建议二次维护，提升复购与拉新。")
        db.execute(
            'INSERT INTO operation_tasks (player_id, task_type, priority, description, status, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [pid, 'feedback_review', 'normal', desc, 'pending', _now()]
        )
        created += 1

    db.commit()
    return {'created': created, 'task_type': 'feedback_review'}


# ===================== 每日经营快照 =====================

def generate_daily_snapshot(db, date_str=None):
    """生成/更新指定日期的经营快照，写入 daily_operation_snapshot（upsert by date）。"""
    if not date_str:
        date_str = (_today() - timedelta(days=1)).isoformat()

    gmv = _gmv_on_date(db, date_str)
    sessions = db.execute(
        "SELECT COUNT(*) c FROM sessions WHERE date(start_time)=?", [date_str]
    ).fetchone()['c']

    pt = db.execute(
        '''SELECT DISTINCT sp.player_id, sp.player_name FROM session_players sp
           JOIN sessions s ON sp.session_id=s.id WHERE date(s.start_time)=?''',
        [date_str]
    ).fetchall()
    players = len(pt)
    new_count = 0
    repeat_count = 0
    for r in pt:
        cnt = db.execute(
            '''SELECT COUNT(*) c FROM session_players sp JOIN sessions s ON sp.session_id=s.id
               WHERE (sp.player_id=? OR sp.player_name=?) AND date(s.start_time) <= ?''',
            [r['player_id'], r['player_name'], date_str]
        ).fetchone()['c']
        if cnt <= 1:
            new_count += 1
        else:
            repeat_count += 1

    fbs = db.execute(
        "SELECT * FROM session_feedback f JOIN sessions s ON f.session_id=s.id WHERE date(s.start_time)=?",
        [date_str]
    ).fetchall()
    avg_score = round(sum(_session_avg_of(db, f['session_id']) for f in fbs) / len(fbs), 2) if fbs else 0.0

    total_tasks = db.execute(
        "SELECT COUNT(*) c FROM operation_tasks WHERE date(created_at)=?", [date_str]
    ).fetchone()['c']
    done_tasks = db.execute(
        "SELECT COUNT(*) c FROM operation_tasks WHERE date(created_at)=? AND status='completed'", [date_str]
    ).fetchone()['c']
    completion_rate = round(done_tasks / total_tasks * 100, 1) if total_tasks else 0.0

    existing = db.execute('SELECT id FROM daily_operation_snapshot WHERE date=?', [date_str]).fetchone()
    if existing:
        db.execute(
            '''UPDATE daily_operation_snapshot
               SET gmv=?, sessions=?, players=?, new_players=?, repeat_players=?,
                   average_table_score=?, task_completion_rate=?
               WHERE date=?''',
            [gmv, sessions, players, new_count, repeat_count, avg_score, completion_rate, date_str]
        )
    else:
        db.execute(
            '''INSERT INTO daily_operation_snapshot
               (date, gmv, sessions, players, new_players, repeat_players,
                average_table_score, task_completion_rate, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [date_str, gmv, sessions, players, new_count, repeat_count, avg_score, completion_rate, _now()]
        )
    db.commit()
    return {'date': date_str, 'gmv': gmv, 'sessions': sessions,
            'average_table_score': avg_score, 'task_completion_rate': completion_rate}


def get_daily_snapshots(db, limit=30):
    rows = db.execute(
        'SELECT * FROM daily_operation_snapshot ORDER BY date DESC LIMIT ?', [limit]
    ).fetchall()
    return [dict(r) for r in rows]
