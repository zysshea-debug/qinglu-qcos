"""青鹭QCOS V1.3 经营反馈闭环 - 玩家组合学习模块
(Player Combination Learning)

基于 session_players + session_feedback 学习「谁和谁一起玩更顺」。
不改动任何已有收银 / 计费 / 会员 / CRM 核心代码，与 V1.1 / V1.2 完全兼容。

对外函数：
    get_pair_stats(db, a, b)              -> 取某无序对的组合统计
    recompute_pair_stats(db)             -> 重算全部组合统计（写入 player_pair_stats）
    get_best_combinations(db, top)       -> 优秀组合库（高评分常同组）
    get_risk_combinations(db, top)       -> 风险组合建议（只建议，绝不自动写 avoid）
"""

from datetime import datetime

# 冲突类型 -> 可读标签
CONFLICT_TYPE_LABELS = {
    'none': '无冲突',
    'skill_gap': '技术差距',
    'personality': '性格不合',
    'money_pressure': '输赢压力',
    'other': '其他',
}


def _now():
    return datetime.now().isoformat()


def get_pair_stats(db, a, b):
    """取两人之间的组合统计（任一方向），无则 None。"""
    r = db.execute(
        'SELECT * FROM player_pair_stats WHERE (player_a_id=? AND player_b_id=?) OR (player_a_id=? AND player_b_id=?)',
        [a, b, b, a]
    ).fetchone()
    return dict(r) if r else None


def _session_avg(f):
    """反馈行的平均桌局评分（氛围/匹配/质量 三项的均值，1-5）。"""
    return (f['atmosphere_score'] + f['compatibility_score'] + f['table_quality_score']) / 3.0


def recompute_pair_stats(db):
    """重算全部玩家组合统计，写入 player_pair_stats（先清空再重建）。

    组合判定：所有“两人同 session 且都有 player_id”的共现计为 play_count；
    其中“该 session 有反馈”的部分用于计算 positive/negative/average_score。
    """
    # 反馈按 session 聚合
    fb = {}
    for f in db.execute(
        'SELECT session_id, atmosphere_score, compatibility_score, table_quality_score, conflict_level '
        'FROM session_feedback'
    ).fetchall():
        avg = _session_avg(f)
        fb[f['session_id']] = {
            'avg': avg,
            'conflict': f['conflict_level'] or 1,
            'positive': avg >= 4 and (f['conflict_level'] or 1) <= 1,
            'negative': avg <= 2 or (f['conflict_level'] or 1) >= 3,
        }

    rows = db.execute(
        '''SELECT sp1.player_id a, sp2.player_id b, s.id sid
           FROM session_players sp1
           JOIN session_players sp2 ON sp1.session_id = sp2.session_id AND sp1.player_id < sp2.player_id
           JOIN sessions s ON sp1.session_id = s.id
           WHERE sp1.player_id IS NOT NULL AND sp2.player_id IS NOT NULL'''
    ).fetchall()

    pair = {}
    for r in rows:
        key = (r['a'], r['b'])
        st = pair.setdefault(key, {'play': 0, 'pos': 0, 'neg': 0, 'sum': 0.0, 'cnt': 0})
        st['play'] += 1
        f = fb.get(r['sid'])
        if f:
            st['cnt'] += 1
            st['sum'] += f['avg']
            if f['positive']:
                st['pos'] += 1
            if f['negative']:
                st['neg'] += 1

    now = _now()
    db.execute('DELETE FROM player_pair_stats')
    for (a, b), st in pair.items():
        avg_score = round(st['sum'] / st['cnt'], 2) if st['cnt'] else 0.0
        if st['pos'] > st['neg']:
            trend = 'improving'
        elif st['neg'] > st['pos']:
            trend = 'declining'
        elif st['play'] > 0:
            trend = 'stable'
        else:
            trend = 'unknown'
        last_play = db.execute(
            '''SELECT MAX(date(s.start_time))
               FROM session_players sp1
               JOIN session_players sp2 ON sp1.session_id = sp2.session_id
               JOIN sessions s ON sp1.session_id = s.id
               WHERE ((sp1.player_id=? AND sp2.player_id=?) OR (sp1.player_id=? AND sp2.player_id=?))''',
            [a, b, b, a]
        ).fetchone()[0]
        db.execute(
            '''INSERT INTO player_pair_stats
               (player_a_id, player_b_id, play_count, positive_count, negative_count,
                average_score, last_play_date, relationship_trend, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            [a, b, st['play'], st['pos'], st['neg'], avg_score, last_play, trend, now]
        )
    db.commit()
    return len(pair)


def get_best_combinations(db, top=10):
    """优秀组合库：找出“有反馈且平均评分高”的真实同桌组合（按玩家集合聚合）。"""
    rows = db.execute(
        '''SELECT f.session_id,
                  (f.atmosphere_score + f.compatibility_score + f.table_quality_score) / 3.0 AS avg,
                  GROUP_CONCAT(DISTINCT sp.player_id) AS pids,
                  COUNT(DISTINCT sp.player_id) AS pc
           FROM session_feedback f
           JOIN session_players sp ON sp.session_id = f.session_id
           WHERE sp.player_id IS NOT NULL
           GROUP BY f.session_id'''
    ).fetchall()

    groups = {}
    for r in rows:
        pids = sorted(int(x) for x in (r['pids'] or '').split(',') if x)
        if len(pids) < 2:
            continue
        key = tuple(pids)
        g = groups.setdefault(key, {'pids': pids, 'count': 0, 'sum': 0.0})
        g['count'] += 1
        g['sum'] += r['avg']

    best = []
    for key, g in groups.items():
        avg_score = round(g['sum'] / g['count'], 2)
        if avg_score < 4:
            continue
        names = []
        for pid in g['pids']:
            nm = db.execute('SELECT name FROM players WHERE id=?', [pid]).fetchone()
            names.append(nm['name'] if nm else f'#{pid}')
        if g['count'] >= 2 and avg_score >= 4.5:
            recommend = 'high'
        elif avg_score >= 4:
            recommend = 'medium'
        else:
            recommend = 'low'
        best.append({
            'players': g['pids'],
            'player_names': names,
            'play_count': g['count'],
            'average_score': avg_score,
            'recommend': recommend,
        })
    best.sort(key=lambda x: (-x['average_score'], -x['play_count']))
    return best[:top]


def get_risk_combinations(db, top=10):
    """风险组合建议（avoid_suggestion）：只生成建议，绝不自动写入 avoid。

    命中条件：平均评分<=2.5，或负向桌局>=2，或 负>=正 且 负>0。
    """
    rows = db.execute(
        '''SELECT * FROM player_pair_stats
           WHERE average_score > 0
             AND ((average_score <= 2.5 AND play_count >= 1)
                  OR negative_count >= 2
                  OR (negative_count >= positive_count AND negative_count > 0))
           ORDER BY negative_count DESC, average_score ASC'''
    ).fetchall()

    out = []
    for r in rows:
        r = dict(r)
        a = db.execute('SELECT name FROM players WHERE id=?', [r['player_a_id']]).fetchone()
        b = db.execute('SELECT name FROM players WHERE id=?', [r['player_b_id']]).fetchone()
        reasons = []
        if r['average_score'] > 0:
            reasons.append(f"历史平均评分 {r['average_score']}")
        if r['negative_count'] > 0:
            reasons.append(f"负向桌局 {r['negative_count']} 次")

        # 补充：该组合历史冲突主因
        ct = db.execute(
            '''SELECT f.conflict_type, COUNT(*) c
               FROM session_feedback f
               JOIN session_players sp1 ON sp1.session_id = f.session_id
               JOIN session_players sp2 ON sp2.session_id = f.session_id
               WHERE ((sp1.player_id=? AND sp2.player_id=?) OR (sp1.player_id=? AND sp2.player_id=?))
                 AND f.conflict_type != 'none'
               GROUP BY f.conflict_type ORDER BY c DESC LIMIT 1''',
            [r['player_a_id'], r['player_b_id'], r['player_b_id'], r['player_a_id']]
        ).fetchone()
        if ct and ct['conflict_type'] in CONFLICT_TYPE_LABELS and ct['conflict_type'] != 'none':
            reasons.append(CONFLICT_TYPE_LABELS[ct['conflict_type']])

        trend_label = {
            'improving': '关系改善', 'declining': '关系下滑',
            'stable': '关系平稳', 'unknown': '关系未知'
        }.get(r['relationship_trend'], '')

        out.append({
            'player_a_id': r['player_a_id'],
            'player_a_name': a['name'] if a else f"#{r['player_a_id']}",
            'player_b_id': r['player_b_id'],
            'player_b_name': b['name'] if b else f"#{r['player_b_id']}",
            'play_count': r['play_count'],
            'average_score': r['average_score'],
            'positive_count': r['positive_count'],
            'negative_count': r['negative_count'],
            'relationship_trend': r['relationship_trend'],
            'trend_label': trend_label,
            'reason': '；'.join(reasons) or '历史同桌体验一般',
            'suggestion': '不建议优先安排同桌',
        })
    return out[:top]
