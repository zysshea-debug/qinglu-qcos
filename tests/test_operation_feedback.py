"""QCOS V1.3 经营反馈闭环 - 单元测试

覆盖：反馈保存 / 评分计算 / 组合统计 / 正负反馈影响匹配 / 人工关系优先级 /
      无反馈数据正常运行 / 任务生成 / 每日快照。

使用临时数据库（基于真实 schema），不污染生产库。
"""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import config
# 指向临时库，避免污染生产数据
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.init_db()
import operation_feedback as fb
import table_learning as tl
import player_matching as pm
import operations as ops

DB = models.get_db()
DB.execute('PRAGMA busy_timeout=30000')

REVIEW_DATE = '2026-08-13'  # 相对当前(8-14)为“昨天”


# ===================== 测试夹具 =====================

def add_player(name):
    cur = DB.execute('INSERT INTO players (name) VALUES (?)', [name])
    DB.commit()
    return cur.lastrowid


def add_session(pid_list, start=REVIEW_DATE + ' 20:00:00', status='closed'):
    cur = DB.execute('INSERT INTO sessions (machine_id, start_time, status) VALUES (1, ?, ?)',
                     [start, status])
    sid = cur.lastrowid
    DB.commit()
    for pid in pid_list:
        DB.execute('INSERT INTO session_players (session_id, player_name, player_id, status) '
                   'VALUES (?, ?, ?, "playing")', [sid, 'p', pid])
    DB.commit()
    return sid


def submit(sid, atm, comp, qual, conflict, ctype='none', notes=''):
    return fb.submit_feedback(DB, sid, {
        'atmosphere_score': atm, 'compatibility_score': comp, 'table_quality_score': qual,
        'conflict_level': conflict, 'conflict_type': ctype, 'notes': notes,
    }, 'tester')


def reset():
    """清空所有相关表，保证测试隔离。"""
    for t in ('session_feedback', 'player_pair_stats', 'operation_tasks',
              'player_relationships', 'session_players', 'sessions', 'players',
              'daily_operation_snapshot'):
        DB.execute(f'DELETE FROM {t}')
    DB.commit()


# ===================== 测试执行 =====================

passed = 0
failed = 0


def check(name, cond, extra=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  ✓ {name}')
    else:
        failed += 1
        print(f'  ✗ {name}  {extra}')


print('=== V1.3 经营反馈闭环测试 ===')

# --- 1. 反馈保存 ---
print('\n[1] 反馈保存')
reset()
A, B = add_player('A伟'), add_player('B强')
s1 = add_session([A, B])
submit(s1, 5, 5, 5, 1)
rows = fb.get_feedback(DB, session_id=s1)
check('反馈可保存', len(rows) == 1, f'got {len(rows)}')
submit(s1, 4, 4, 4, 1)  # 同一桌重提应为 upsert
rows2 = fb.get_feedback(DB, session_id=s1)
check('同桌反馈 upsert 不重复', len(rows2) == 1, f'got {len(rows2)}')

# --- 2. 评分计算（正向提升 / 负向下降）---
print('\n[2] 评分计算')
reset()
A = add_player('A伟')
s_pos = add_session([A])
submit(s_pos, 5, 5, 5, 1)
fb.recompute_experience(DB)
pa = DB.execute('SELECT * FROM players WHERE id=?', [A]).fetchone()
check('正向反馈 → 体验分高(>=70)', (pa['experience_score'] or 0) >= 70, f"score={pa['experience_score']}")
check('正向反馈 → positive_table_count>=1', (pa['positive_table_count'] or 0) >= 1)

reset()
A = add_player('A伟')
s_neg = add_session([A])
submit(s_neg, 1, 1, 2, 4, 'skill_gap')
fb.recompute_experience(DB)
pa = DB.execute('SELECT * FROM players WHERE id=?', [A]).fetchone()
_neg_score = pa['experience_score']
check('负向反馈 → 体验分下降(<70)', (_neg_score if _neg_score is not None else 100) < 70, f"score={_neg_score}")
check('负向反馈 → negative_table_count>=1', (pa['negative_table_count'] or 0) >= 1)
check('负向反馈 → conflict_count>=1', (pa['conflict_count'] or 0) >= 1)

reset()
A = add_player('A伟')
s_pos = add_session([A]); submit(s_pos, 5, 5, 5, 1)
_pos_score = DB.execute('SELECT experience_score FROM players WHERE id=?', [A]).fetchone()[0]
s_neg = add_session([A]); submit(s_neg, 1, 1, 2, 4, 'skill_gap')
fb.recompute_experience(DB)
pa = DB.execute('SELECT * FROM players WHERE id=?', [A]).fetchone()
_mix_score = pa['experience_score']
check('正>负 时体验分处中区间(0<score<70)', 0 < (_mix_score or 0) < 70, f"score={_mix_score}")
check('正向拉高 > 负向拉低（方向正确）', _pos_score > _mix_score > _neg_score,
      f"pos={_pos_score} mix={_mix_score} neg={_neg_score}")

# --- 3. 组合统计 ---
print('\n[3] 组合统计')
reset()
A, B = add_player('A伟'), add_player('B强')
s1 = add_session([A, B]); submit(s1, 5, 5, 5, 1)
s2 = add_session([A, B]); submit(s2, 1, 2, 2, 4, 'skill_gap')
tl.recompute_pair_stats(DB)
ps = tl.get_pair_stats(DB, A, B)
check('组合统计存在', ps is not None)
check('play_count>=2', (ps['play_count'] or 0) >= 2, f"play={ps['play_count']}")
check('同时含正向与负向计数', (ps['positive_count'] or 0) >= 1 and (ps['negative_count'] or 0) >= 1)

# --- 4. 正负反馈影响匹配 ---
print('\n[4] 正负反馈影响匹配')
reset()
# 负向组合 A-B（2 次负向）
A, B = add_player('A伟'), add_player('B强')
for _ in range(2):
    sid = add_session([A, B]); submit(sid, 1, 1, 2, 4, 'skill_gap')
# 正向组合 C-D（2 次正向）
C, D = add_player('C丽'), add_player('D杰')
for _ in range(2):
    sid = add_session([C, D]); submit(sid, 5, 5, 5, 1)
tl.recompute_pair_stats(DB)

res_neg = pm.match_table(DB, 'competitive', [B], 1)
a_in_neg = next((x for x in res_neg['not_recommended'] if x['player_id'] == A), None)
check('负向历史 → A 被排除(avoid)', a_in_neg is not None, f"not_rec={[x['name'] for x in res_neg['not_recommended']]}")
if a_in_neg:
    check('排除原因含历史体验差', '历史' in (a_in_neg['reasons'][0] or ''), str(a_in_neg['reasons']))

res_pos = pm.match_table(DB, 'competitive', [D], 1)
c_in_pos = next((x for x in res_pos['recommended'] if x['player_id'] == C), None)
check('正向历史 → C 被推荐', c_in_pos is not None)
if c_in_pos:
    check('正向历史 → history_score>0', (c_in_pos['history_score'] or 0) > 0, f"hist={c_in_pos['history_score']}")

# --- 5. 人工关系优先级 ---
print('\n[5] 人工关系优先级')
reset()
E, F = add_player('E哥'), add_player('F妹')
# 自动建议（positive）
DB.execute("INSERT INTO player_relationships (player_a_id,player_b_id,relationship_type,relationship_score,note,source,created_at,updated_at) "
           "VALUES (?,?,?,?,?,?,?,?)", [E, F, 'positive', 60, '自动', 'auto', '2026-08-01', '2026-08-01'])
DB.commit()
rel_auto = pm.get_pair_relationship(DB, E, F)
check('仅有自动建议时返回 auto', rel_auto['source'] == 'auto')
# 人工覆盖（avoid）
pm.save_relationship(DB, E, F, 'avoid', -100, '人工确认冲突', 'admin')
rel_manual = pm.get_pair_relationship(DB, E, F)
check('人工关系优先于自动', rel_manual['source'] == 'manual' and rel_manual['relationship_type'] == 'avoid')
# 匹配中人工 avoid 生效
res = pm.match_table(DB, 'competitive', [F], 1)
e_in = next((x for x in res['not_recommended'] if x['player_id'] == E), None)
check('人工 avoid → E 被排除', e_in is not None)

# --- 6. 无反馈数据正常运行 ---
print('\n[6] 无反馈数据正常运行')
reset()
P1, P2 = add_player('阿一'), add_player('阿二')
add_session([P1, P2])  # 有局无反馈
res = pm.match_table(DB, 'competitive', [P2], 1)
check('无反馈时匹配不崩溃', res is not None and 'recommended' in res)
cand = next((x for x in res['recommended'] if x['player_id'] == P1), None)
check('无反馈时 history_score=0', cand is None or (cand['history_score'] or 0) == 0)
rev = fb.get_operation_review(DB, REVIEW_DATE)
check('无反馈复盘不崩溃', rev is not None and rev['average_table_score'] == 0)
check('无反馈优秀组合为空', len(tl.get_best_combinations(DB, 10)) == 0)
check('无反馈风险组合为空(不误判)', len(tl.get_risk_combinations(DB, 10)) == 0)

# --- 7. 任务生成 + 每日快照 ---
print('\n[7] 任务生成 + 每日快照')
reset()
# 风险组合：A-B 2 次负向，且近14天
A, B = add_player('A伟'), add_player('B强')
for _ in range(2):
    sid = add_session([A, B]); submit(sid, 1, 1, 2, 4, 'skill_gap')
tl.recompute_pair_stats(DB)
# 新客单次正向
N = add_player('新客小新')
sn = add_session([N]); submit(sn, 5, 5, 5, 1)
summary = fb.generate_feedback_review_tasks(DB)
check('反馈复盘任务被生成', summary.get('created', 0) > 0, f"created={summary}")
tasks = DB.execute("SELECT * FROM operation_tasks WHERE task_type='feedback_review'").fetchall()
check('operation_tasks 含 feedback_review', len(tasks) > 0, f"n={len(tasks)}")
# 每日快照
snap = fb.generate_daily_snapshot(DB, REVIEW_DATE)
check('每日快照生成', snap['date'] == REVIEW_DATE)
snaps = fb.get_daily_snapshots(DB, 10)
check('快照可读取', len(snaps) >= 1)
# 复盘聚合能拿到组合
fb.recompute_experience(DB)
rev = fb.get_operation_review(DB, REVIEW_DATE)
check('复盘含风险组合', len(rev['risk_combinations']) > 0, f"risk={len(rev['risk_combinations'])}")

# ===================== 清理 & 汇总 =====================
DB.close()
try:
    if os.path.exists(_tmp.name):
        os.remove(_tmp.name)
except Exception:
    pass

print(f'\n=== 结果：通过 {passed} / 失败 {failed} ===')
if failed > 0:
    sys.exit(1)
