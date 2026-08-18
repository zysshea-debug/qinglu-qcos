"""青鹭QCOS V1.2 玩家关系网络 + 智能组局匹配引擎
(Player Relationship Network & Smart Table Matching Engine)

本模块为纯新增独立逻辑，不改动任何已有收银 / 计费 / 会员 / CRM 核心代码，
与 V1.1 operations.py 完全兼容（仅新增 players 字段与 player_relationships 表）。

设计原则（来自青鹭社区型经营需求）：
    组局成功率不只取决于「是否有时间 / 是否活跃」，
    更取决于「是否愿意与他人同桌 / 主动被动 / 局型偏好 / 隐性冲突」。

对外函数：
    compute_initiative(db)                 -> 重算所有玩家主动性，写回 players
    compute_table_style(db)               -> 重算所有玩家局型偏好，写回 players（人工优先）
    analyze_relationships(db)            -> 生成关系建议（只建议，绝不写 avoid）
    get_relationships(db, player_id)      -> 某玩家全部人工关系（含对方姓名）
    get_relationship_suggestions(db, pid) -> 某玩家基于历史的同桌建议（不入库）
    save_relationship(db, a, b, rtype, score, note, operator) -> 人工写入关系
    match_table(db, table_style, existing_ids, missing_count, stake) -> 推荐/不推荐
    generate_table_match_tasks(db, table_style, existing_ids, missing_count, operator) -> 汇总
"""

from datetime import datetime, date, timedelta
from table_learning import get_pair_stats


# ===================== 常量 =====================

INITIATIVE_LEVELS = {
    'active': '主动型',
    'semi_active': '半主动',
    'passive': '被动型',
    'unknown': '未知',
}

# 详情/工作台展示用的 A/B/C 级（主动性）
INITIATIVE_TIERS = {
    'active': 'A',
    'semi_active': 'B',
    'passive': 'C',
    'unknown': '?',
}

TABLE_STYLES = {
    'competitive': '竞技型',
    'entertainment': '娱乐型',
    'social': '社交型',
    'high_variance': '高波动型',
    'unknown': '未知',
}

REL_TYPES = {
    'positive': '喜欢一起',
    'neutral': '普通关系',
    'avoid': '避免同桌',
}

TABLE_MATCH_LABELS = {
    'competitive': '竞技局',
    'entertainment': '娱乐局',
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


def _now():
    return datetime.now().isoformat()


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ===================== 玩家信号聚合 =====================

def _get_signals(db, pid, pname):
    """聚合单个玩家的主动性信号、局型信号、历史到店、带客、组织等。"""
    pname = pname or ''
    dates = []
    proactive = 0          # 主动行为次数（主动到店 + 带朋友）
    is_organizer = 0       # 主动组织次数
    last_active_flag = False

    sps = db.execute(
        '''SELECT s.start_time, sp.visit_type, sp.is_organizer
           FROM session_players sp JOIN sessions s ON sp.session_id = s.id
           WHERE (sp.player_id = ? OR sp.player_name = ?)''',
        [pid, pname]
    ).fetchall()
    for r in sps:
        dt = _parse_dt(r['start_time'])
        if dt:
            dates.append(dt)
        if (r['visit_type'] or 'active') == 'active':
            proactive += 1
        if r['is_organizer']:
            is_organizer += 1

    vrs = db.execute(
        '''SELECT visit_date, game_type, brought_guest, is_table_head, organizer_name
           FROM visit_records WHERE player_id = ? OR player_name = ?''',
        [pid, pname]
    ).fetchall()
    for r in vrs:
        dt = _parse_dt(r['visit_date'])
        if dt:
            dates.append(dt)
        if r['brought_guest']:
            proactive += 1
        if r['is_table_head'] or (r['organizer_name'] and r['organizer_name'] != '无'):
            is_organizer += 1

    # 最近一次到店是否为主动型
    last_sp = db.execute(
        '''SELECT sp.visit_type FROM session_players sp JOIN sessions s ON sp.session_id = s.id
           WHERE (sp.player_id = ? OR sp.player_name = ?) ORDER BY s.start_time DESC LIMIT 1''',
        [pid, pname]
    ).fetchone()
    if last_sp and (last_sp['visit_type'] or 'active') == 'active':
        last_active_flag = True

    comp = db.execute(
        "SELECT COUNT(*) c FROM visit_records WHERE (player_id = ? OR player_name = ?) AND game_type = '竞技'",
        [pid, pname]
    ).fetchone()['c']
    cas = db.execute(
        "SELECT COUNT(*) c FROM visit_records WHERE (player_id = ? OR player_name = ?) AND game_type = '娱乐'",
        [pid, pname]
    ).fetchone()['c']
    brought = db.execute(
        "SELECT COUNT(*) c FROM visit_records WHERE (player_id = ? OR player_name = ?) AND brought_guest = 1",
        [pid, pname]
    ).fetchone()['c']

    return {
        'dates': dates,
        'proactive': proactive,
        'is_organizer': is_organizer,
        'last_active_flag': last_active_flag,
        'competitive': comp,
        'casual': cas,
        'brought_guests': brought,
    }


# ===================== 玩家主动性模型 =====================

def _score_initiative(sig, today):
    """返回 (initiative_score, initiative_level)。
    评分：主动联系行为 +30 / 历史主动组织 +30 / 最近主动到店 +20 / 带朋友 +20（满分100）。
    映射：>=70 主动型 / 40-69 半主动 / <40 被动型 / 无数据 unknown。
    """
    has_data = len(sig['dates']) > 0
    if not has_data:
        return 0, 'unknown'

    # A 主动联系行为 +30
    if sig['proactive'] >= 3:
        a = 30
    elif sig['proactive'] >= 1:
        a = 20
    else:
        a = 0

    # B 历史主动组织 +30
    b = 30 if sig['is_organizer'] >= 1 else 0

    # C 最近主动到店 +20
    last = max(sig['dates']).date()
    recency = (today - last).days
    if sig['last_active_flag']:
        if recency <= 14:
            c = 20
        elif recency <= 30:
            c = 12
        else:
            c = 6
    else:
        c = 0

    # D 带朋友 +20
    if sig['brought_guests'] >= 2:
        d = 20
    elif sig['brought_guests'] >= 1:
        d = 12
    else:
        d = 0

    total = min(100, a + b + c + d)

    if total >= 70:
        level = 'active'
    elif total >= 40:
        level = 'semi_active'
    else:
        level = 'passive'
    return total, level


def compute_initiative(db):
    """重算所有玩家主动性，写回 players 表。"""
    players = db.execute('SELECT * FROM players').fetchall()
    today = _today()
    results = []
    for p in players:
        p = dict(p)
        sig = _get_signals(db, p['id'], p.get('name'))
        score, level = _score_initiative(sig, today)
        db.execute(
            'UPDATE players SET initiative_score=?, initiative_level=?, initiative_updated=? WHERE id=?',
            [score, level, _now(), p['id']]
        )
        results.append({'player_id': p['id'], 'name': p.get('name'),
                        'initiative_score': score, 'initiative_level': level})
    db.commit()
    return results


# ===================== 玩家局型画像 =====================

def _infer_table_style(sig):
    """基于历史 game_type 推断局型偏好（人工标签优先，本函数仅用于自动补全）。"""
    comp = sig['competitive']
    cas = sig['casual']
    if comp == 0 and cas == 0:
        return 'unknown'
    # 两种都明显 -> 高波动（混合偏好）
    if comp >= 2 and cas >= 2:
        return 'high_variance'
    if comp > cas:
        return 'competitive' if comp >= 2 else 'social'
    if cas > comp:
        return 'entertainment' if cas >= 2 else 'social'
    if comp >= 2:
        return 'competitive'
    if cas >= 2:
        return 'entertainment'
    return 'social'


def compute_table_style(db):
    """重算所有玩家局型偏好，写回 players（人工已设置的保留，不覆盖）。"""
    players = db.execute('SELECT * FROM players').fetchall()
    results = []
    for p in players:
        p = dict(p)
        existing = p.get('table_style_preference') or 'unknown'
        if existing and existing != 'unknown':
            # 人工标签优先
            results.append({'player_id': p['id'], 'name': p.get('name'),
                            'table_style_preference': existing, 'source': 'manual'})
            continue
        sig = _get_signals(db, p['id'], p.get('name'))
        style = _infer_table_style(sig)
        db.execute(
            'UPDATE players SET table_style_preference=? WHERE id=?',
            [style, p['id']]
        )
        results.append({'player_id': p['id'], 'name': p.get('name'),
                        'table_style_preference': style, 'source': 'auto'})
    db.commit()
    return results


# ===================== 玩家关系分析 =====================

def _cooccurrence(db, a, b):
    """返回 (共同同桌次数, 最近共同日期)。基于 session_players 同 session 出现。"""
    rows = db.execute(
        '''SELECT s.start_time FROM session_players sp1
           JOIN session_players sp2 ON sp1.session_id = sp2.session_id
           JOIN sessions s ON sp1.session_id = s.id
           WHERE ((sp1.player_id = ? AND sp2.player_id = ?) OR (sp1.player_id = ? AND sp2.player_id = ?))
             AND sp1.player_id IS NOT NULL AND sp2.player_id IS NOT NULL''',
        [a, b, b, a]
    ).fetchall()
    cnt = len(rows)
    last = None
    for r in rows:
        dt = _parse_dt(r['start_time'])
        if dt and (last is None or dt > last):
            last = dt
    return cnt, last


def _cooccurrence_score(cnt, last_days):
    """共同同桌 -> 关系加分（0~100区间内的建议分）。绝不返回负数（避免由自动生成）。"""
    if cnt >= 8 and last_days <= 30:
        return 70
    if cnt >= 5 and last_days <= 60:
        return 55
    if cnt >= 3 and last_days <= 90:
        return 40
    if cnt >= 1 and last_days <= 90:
        return 20
    return 5  # 很久没一起，建议关注（中性偏弱）


def analyze_relationships(db):
    """生成全量关系建议（只建议，绝不写 avoid）。返回建议列表。"""
    rows = db.execute(
        '''SELECT sp1.player_id a, sp2.player_id b, s.start_time
           FROM session_players sp1
           JOIN session_players sp2 ON sp1.session_id = sp2.session_id AND sp1.player_id < sp2.player_id
           JOIN sessions s ON sp1.session_id = s.id
           WHERE sp1.player_id IS NOT NULL AND sp2.player_id IS NOT NULL'''
    ).fetchall()
    today = _today()
    pair = {}
    for r in rows:
        key = (r['a'], r['b'])
        st = pair.setdefault(key, {'count': 0, 'last': None})
        st['count'] += 1
        dt = _parse_dt(r['start_time'])
        if dt and (st['last'] is None or dt > st['last']):
            st['last'] = dt

    suggestions = []
    for (a, b), st in pair.items():
        cnt = st['count']
        last = st['last']
        last_days = (today - last.date()).days if last else 999
        score = _cooccurrence_score(cnt, last_days)
        if cnt >= 3 and last_days <= 60:
            rtype = 'positive'
            note = f"共同同桌{cnt}次，关系强度+{score}"
        else:
            rtype = 'neutral'
            note = f"共同出现{cnt}次，最近{last_days}天无共同桌，建议关注"
        suggestions.append({
            'player_a_id': a, 'player_b_id': b,
            'relationship_type': rtype, 'relationship_score': score,
            'co_count': cnt, 'last_co_days': last_days, 'note': note,
        })
    return suggestions


def get_relationships(db, player_id):
    """取某玩家全部人工关系（含对方姓名）。人工优先级高于自动。"""
    rows = db.execute(
        '''SELECT r.*,
                  CASE WHEN r.player_a_id = ? THEN b.name ELSE a.name END AS other_name,
                  CASE WHEN r.player_a_id = ? THEN r.player_b_id ELSE r.player_a_id END AS other_id
           FROM player_relationships r
           LEFT JOIN players a ON r.player_a_id = a.id
           LEFT JOIN players b ON r.player_b_id = b.id
           WHERE r.player_a_id = ? OR r.player_b_id = ?
           ORDER BY r.relationship_score DESC''',
        [player_id, player_id, player_id, player_id]
    ).fetchall()
    return [dict(r) for r in rows]


def get_relationship_suggestions(db, player_id, top=6):
    """取某玩家基于历史的同桌建议（不入库，仅展示）。"""
    today = _today()
    rows = db.execute(
        '''SELECT DISTINCT sp2.player_id other_id
           FROM session_players sp1
           JOIN session_players sp2 ON sp1.session_id = sp2.session_id AND sp1.player_id != sp2.player_id
           WHERE sp1.player_id = ? AND sp2.player_id IS NOT NULL''',
        [player_id]
    ).fetchall()
    out = []
    for r in rows:
        other = r['other_id']
        if other == player_id:
            continue
        cnt, last = _cooccurrence(db, player_id, other)
        if cnt == 0:
            continue
        last_days = (today - last.date()).days if last else 999
        score = _cooccurrence_score(cnt, last_days)
        name = db.execute('SELECT name FROM players WHERE id=?', [other]).fetchone()
        out.append({
            'other_id': other,
            'other_name': name['name'] if name else f'#{other}',
            'co_count': cnt,
            'last_co_days': last_days,
            'relationship_score': score,
            'relationship_type': 'positive' if (cnt >= 3 and last_days <= 60) else 'neutral',
            'note': f"共同同桌{cnt}次，最近{last_days}天",
        })
    out.sort(key=lambda x: -x['relationship_score'])
    return out[:top]


def get_pair_relationship(db, a, b):
    """取两人之间的关系记录（任一方向）。人工优先；若无则返回 None。"""
    rows = db.execute(
        'SELECT * FROM player_relationships WHERE (player_a_id=? AND player_b_id=?) OR (player_a_id=? AND player_b_id=?)',
        [a, b, b, a]
    ).fetchall()
    if not rows:
        return None
    # 人工优先，其次评分最高
    manual = [dict(r) for r in rows if r['source'] == 'manual']
    if manual:
        return manual[0]
    return dict(rows[0])


def save_relationship(db, a_id, b_id, rtype, score, note, operator):
    """人工写入一条关系（同一无序对只保留一条，自动覆盖）。返回记录 id。"""
    if a_id == b_id:
        return None
    # 删除该无序对已存在的记录（保证唯一）
    db.execute(
        'DELETE FROM player_relationships WHERE (player_a_id=? AND player_b_id=?) OR (player_a_id=? AND player_b_id=?)',
        [a_id, b_id, b_id, a_id]
    )
    now = _now()
    cur = db.execute(
        '''INSERT INTO player_relationships
           (player_a_id, player_b_id, relationship_type, relationship_score, note, source, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'manual', ?, ?)''',
        [a_id, b_id, rtype, score, note, now, now]
    )
    db.commit()
    return cur.lastrowid


# ===================== 匹配评分模型 =====================

def _attendance_score(sig, today):
    """到店概率 25分：就近度(0-13) + 频率(0-12)。"""
    dates = sig['dates']
    if not dates:
        return 0, ['无到店记录，到店概率低']
    last = max(dates).date()
    recency = (today - last).days
    if recency <= 7:
        rec = 13
    elif recency <= 14:
        rec = 10
    elif recency <= 30:
        rec = 6
    else:
        rec = 0
    rec_reason = f"最近到店{recency}天前（就近度{'高' if rec>=10 else '中' if rec>=6 else '低'}）"

    visits_30 = sum(1 for d in dates if 0 <= (today - d.date()).days <= 30)
    if visits_30 >= 5:
        freq = 12
    elif visits_30 >= 3:
        freq = 9
    elif visits_30 >= 1:
        freq = 6
    else:
        freq = 0
    freq_reason = f"近30天到店{visits_30}次（频率{'高' if freq>=9 else '中' if freq>=6 else '低'}）"

    return rec + freq, [rec_reason, freq_reason]


def _style_match(style, table_style):
    """局型匹配 20分。"""
    if style == table_style:
        return 20, f"局型匹配（{TABLE_STYLES.get(style, style)}一致）"
    if style == 'unknown':
        return 12, "局型未知，中性匹配"
    if style == 'high_variance':
        return 16, "高波动型，适配多种局"
    if style == 'social':
        return 12, "社交型，局型中性"
    # 明确冲突
    return 6, f"局型冲突（玩家偏好{TABLE_STYLES.get(style, style)}，本局{TABLE_MATCH_LABELS.get(table_style, table_style)}）"


def _relationship_component(db, pid, existing_ids, names):
    """玩家关系 20分（封顶）。返回 (score, reasons, avoid_with_names)。"""
    today = _today()
    score = 0
    reasons = []
    avoid_with = []
    for eid in existing_ids:
        rel = get_pair_relationship(db, pid, eid)
        ename = names.get(eid, f'#{eid}')
        if rel is None:
            cnt, last = _cooccurrence(db, pid, eid)
            if cnt > 0:
                last_days = (today - last.date()).days if last else 999
                s = _cooccurrence_score(cnt, last_days)
                contrib = min(8, s // 7)
                if contrib > 0:
                    score += contrib
                    reasons.append(f"与{ename}历史同桌{cnt}次（关系+）")
            continue
        if rel['relationship_type'] == 'avoid' or rel['relationship_score'] < 0:
            avoid_with.append(ename)
        elif rel['relationship_type'] == 'positive':
            contrib = min(8, abs(rel['relationship_score']) // 7)
            if contrib > 0:
                score += contrib
                reasons.append(f"与{ename}关系良好（人工标注+）")
        # neutral -> 0
    score = min(20, score)
    return score, reasons, avoid_with


def _historical_performance_component(db, pid, existing_ids, names):
    """历史桌局表现 20分（封顶）。基于 player_pair_stats（来自 session_feedback 学习）。
    返回 (score, reasons, avoid_with_names)。
    过去组合反馈好 -> 加分；差 -> 扣分；强负向 -> 建议排除。
    """
    score = 0
    reasons = []
    avoid_with = []
    for eid in existing_ids:
        ename = names.get(eid, f'#{eid}')
        if pid == eid:
            continue
        ps = get_pair_stats(db, pid, eid)
        if not ps:
            continue
        avg = ps['average_score'] or 0
        neg = ps['negative_count'] or 0
        pos = ps['positive_count'] or 0
        if avg <= 0:
            continue  # 该组合尚无反馈数据，按中性处理，不加分不扣分
        if neg >= 2 and avg <= 2.5:
            score -= min(10, neg * 5)
            reasons.append(f"与{ename}历史同桌体验差（平均{avg}，负向{neg}次）")
            avoid_with.append(ename)
        elif avg >= 4 and pos >= 1:
            contrib = min(10, int(avg / 5 * 10))
            score += contrib
            reasons.append(f"与{ename}历史同桌{ps['play_count']}次，平均评分{avg}（历史+）")
        elif avg <= 2.5 or neg >= 1:
            score -= min(8, neg * 4 + 2)
            reasons.append(f"与{ename}历史同桌体验一般（平均{avg}）")
        # 中性（avg 在 2.5~4 且无明显负向）不调整
    score = _clamp(score, 0, 20)
    return score, reasons, avoid_with


def match_table(db, table_style, existing_ids, missing_count, stake=None):
    """智能组局匹配核心。
    参数：
        table_style: 'competitive' / 'entertainment'
        existing_ids: 已有玩家 id 列表
        missing_count: 还差几个人
        stake: 'K5'/'K10'/'K20'（仅用于描述，高注偏好高波动型）
    返回：{recommended:[...], not_recommended:[...]}
    """
    today = _today()
    existing_ids = [int(x) for x in existing_ids if x]
    names = {}
    for eid in existing_ids:
        nm = db.execute('SELECT name FROM players WHERE id=?', [eid]).fetchone()
        names[eid] = nm['name'] if nm else f'#{eid}'

    candidates = db.execute('SELECT * FROM players').fetchall()
    recommended = []
    not_recommended = []

    for p in candidates:
        p = dict(p)
        pid = p['id']
        if pid in existing_ids:
            continue
        sig = _get_signals(db, pid, p.get('name'))

        # 1) 到店概率 25
        att, att_reasons = _attendance_score(sig, today)
        # 2) 主动意愿 15
        init_level = p.get('initiative_level') or 'unknown'
        will = {'active': 15, 'semi_active': 9, 'passive': 5, 'unknown': 8}[init_level]
        will_reason = f"{INITIATIVE_LEVELS[init_level]}（主动意愿{will}分）"
        # 3) 局型匹配 20
        style = p.get('table_style_preference') or 'unknown'
        sm, sm_reason = _style_match(style, table_style)
        # 4) 关系 20
        rel_score, rel_reasons, rel_avoid = _relationship_component(db, pid, existing_ids, names)
        # 5) 历史桌局表现 20
        hist_score, hist_reasons, hist_avoid = _historical_performance_component(db, pid, existing_ids, names)

        avoid_with = list(set(rel_avoid + hist_avoid))

        # 强制排除：avoid 关系 / 历史强负向组合
        if avoid_with:
            not_recommended.append({
                'player_id': pid, 'name': p.get('name'),
                'initiative_level': init_level, 'initiative_tier': INITIATIVE_TIERS.get(init_level),
                'table_style': style, 'table_style_label': TABLE_STYLES.get(style, style),
                'score': 0,
                'reasons': [f"与{', '.join(avoid_with)}存在避免同桌关系或历史体验差"],
                'reason_type': 'avoid',
            })
            continue

        # 强制排除：局型冲突（已知偏好且与本局相反，且非高波动/社交）
        if style in ('competitive', 'entertainment') and style != table_style:
            not_recommended.append({
                'player_id': pid, 'name': p.get('name'),
                'initiative_level': init_level, 'initiative_tier': INITIATIVE_TIERS.get(init_level),
                'table_style': style, 'table_style_label': TABLE_STYLES.get(style, style),
                'score': att + will + sm + rel_score + hist_score,
                'reasons': [sm_reason, '局型冲突，不建议同桌'],
                'reason_type': 'style_conflict',
            })
            continue

        total = max(0, min(100, att + will + sm + rel_score + hist_score))
        reasons = list(att_reasons) + [will_reason, sm_reason] + rel_reasons + hist_reasons
        if init_level == 'passive':
            reasons.append("被动型，需要主动邀请")
        if p.get('conflict_count') and p['conflict_count'] >= 2:
            reasons.append(f"该玩家历史冲突桌局 {p['conflict_count']} 次，需留意")
        if not reasons:
            reasons.append("基础数据较少，按中性评估")
        recommended.append({
            'player_id': pid, 'name': p.get('name'),
            'initiative_level': init_level, 'initiative_tier': INITIATIVE_TIERS.get(init_level),
            'table_style': style, 'table_style_label': TABLE_STYLES.get(style, style),
            'attendance_score': att, 'willingness_score': will,
            'style_score': sm, 'relation_score': rel_score, 'history_score': hist_score,
            'experience_score': p.get('experience_score'),
            'compatibility_score': p.get('compatibility_score'),
            'score': total, 'reasons': reasons,
        })

    recommended.sort(key=lambda x: -x['score'])
    not_recommended.sort(key=lambda x: x['name'])
    return {
        'table_style': table_style,
        'table_style_label': TABLE_MATCH_LABELS.get(table_style, table_style),
        'stake': stake,
        'existing_ids': existing_ids,
        'missing_count': missing_count,
        'recommended': recommended,
        'recommended_top': recommended[:max(0, missing_count)] if missing_count else recommended[:5],
        'not_recommended': not_recommended,
    }


# ===================== 联动运营任务 =====================

def generate_table_match_tasks(db, table_style, existing_ids, missing_count, operator='system'):
    """对推荐列表中前 missing_count 人生成 table_match 运营任务（high 优先级）。"""
    today = _today()
    res = match_table(db, table_style, existing_ids, missing_count)
    top = res['recommended_top']
    created = 0
    skipped = 0
    style_label = TABLE_MATCH_LABELS.get(table_style, table_style)
    stake = res.get('stake') or ''
    stake_text = f"{stake}" if stake else ""
    for cand in top:
        # 去重：今日已有该玩家的 pending table_match 任务则不重复
        exist = db.execute(
            "SELECT id FROM operation_tasks WHERE player_id=? AND task_type='table_match' AND status='pending' AND date(created_at)=?",
            [cand['player_id'], today.isoformat()]
        ).fetchone()
        if exist:
            skipped += 1
            continue
        desc = (f"【组局匹配】今晚{stake_text}{style_label}缺{int(missing_count)}人，"
                f"建议联系{cand['name']}（匹配{cand['score']}分，{cand['initiative_level']}）。"
                f"理由：{'；'.join(cand['reasons'][:3])}")
        db.execute(
            'INSERT INTO operation_tasks (player_id, task_type, priority, description, status, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            [cand['player_id'], 'table_match', 'high', desc, 'pending', _now()]
        )
        created += 1
    db.commit()
    return {'created': created, 'skipped_existing': skipped, 'total': created,
            'candidates': [c['name'] for c in top]}
