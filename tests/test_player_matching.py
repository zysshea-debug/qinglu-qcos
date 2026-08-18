"""QCOS V1.2 玩家关系网络 + 智能组局匹配 单元测试

测试覆盖：
1. 主动型识别
2. 被动型识别
3. 关系加减分
4. avoid 过滤
5. 推荐排序
6. 无数据情况
7. 人工备注优先级
8. 任务生成

使用临时数据库（复用真实 schema），不污染生产数据。
"""

import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timedelta

# 将项目根目录加入路径，确保能 import config / models / player_matching
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 测试专用环境变量（必须在 import config / models 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import config
import models

# 指向临时库，复用真实 schema（含 V1.2 表）
_tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_tmp.close()
config.DB_PATH = _tmp.name
models.DB_PATH = _tmp.name
models.init_db()

import player_matching as pm

DB = sqlite3.connect(_tmp.name)
DB.row_factory = sqlite3.Row
DB.execute('PRAGMA foreign_keys=OFF')


def add_player(name):
    cur = DB.execute('INSERT INTO players (name) VALUES (?)', [name])
    DB.commit()
    return cur.lastrowid


def add_session(players, days_ago=0, status='closed'):
    """players: [(pid, name, visit_type, is_organizer), ...]"""
    start = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d %H:%M:%S')
    cur = DB.execute(
        "INSERT INTO sessions (machine_id, start_time, status) VALUES (1, ?, ?)",
        [start, status])
    sid = cur.lastrowid
    for pid, name, vtype, org in players:
        DB.execute(
            'INSERT INTO session_players (session_id, player_id, player_name, visit_type, is_organizer) VALUES (?,?,?,?,?)',
            [sid, pid, name, vtype, org])
    DB.commit()
    return sid


def add_visit(pid, name, game_type, brought=0, days_ago=0):
    d = (datetime.now() - timedelta(days=days_ago)).strftime('%Y-%m-%d')
    DB.execute(
        "INSERT INTO visit_records (player_id, player_name, visit_date, game_type, brought_guest) VALUES (?,?,?,?,?)",
        [pid, name, d, game_type, brought])
    DB.commit()


def check(name, cond, extra=''):
    status = 'PASS' if cond else 'FAIL'
    print(f'[{status}] {name} {extra}')
    assert cond, f'{name} FAILED {extra}'


# ===== 准备测试数据 =====
# p1 主动型：多次主动到店 + 组织者 + 带人 + 近期
p1 = add_player('阿伟')
add_session([(p1, '阿伟', 'active', 1)], days_ago=2)
add_session([(p1, '阿伟', 'active', 1)], days_ago=5)
add_session([(p1, '阿伟', 'active', 0)], days_ago=9)
add_visit(p1, '阿伟', '竞技', brought=2, days_ago=3)

# p2 被动型：仅被动到店，无组织，久远
p2 = add_player('小敏')
add_session([(p2, '小敏', 'passive', 0)], days_ago=20)
add_visit(p2, '小敏', '娱乐', brought=0, days_ago=20)

# p3 半主动：1次主动到店，但无组织者（需要邀请）
p3 = add_player('老陈')
add_session([(p3, '老陈', 'active', 0)], days_ago=3)
add_visit(p3, '老陈', '竞技', brought=0, days_ago=3)

# p4 无特殊：与 p1 多次共同同桌（自动正向）
p4 = add_player('阿强')
add_session([(p1, '阿伟', 'active', 1), (p4, '阿强', 'active', 0)], days_ago=4)
add_session([(p1, '阿伟', 'active', 1), (p4, '阿强', 'active', 0)], days_ago=10)
add_visit(p4, '阿强', '竞技', brought=0, days_ago=4)

# p5 无数据：完全空白（用于无数据测试）
p5 = add_player('新人')

# p6 规避对象：与 p1 建立人工 avoid
p6 = add_player('阿浩')
add_visit(p6, '阿浩', '娱乐', brought=0, days_ago=5)


# ===== 1. 主动型识别 =====
res = pm.compute_initiative(DB)
m = {r['player_id']: r for r in res}
check('1. 主动型识别 (p1=active)', m[p1]['initiative_level'] == 'active', f"level={m[p1]['initiative_level']}")

# ===== 2. 被动型识别 =====
check('2. 被动型识别 (p2=passive)', m[p2]['initiative_level'] == 'passive', f"level={m[p2]['initiative_level']}")
# 半主动
check('2b. 半主动识别 (p3=semi_active)', m[p3]['initiative_level'] == 'semi_active', f"level={m[p3]['initiative_level']}")

# ===== 3. 关系加减分 =====
cnt, last = pm._cooccurrence(DB, p1, p4)
check('3. 共同同桌计数 (p1,p4>=2)', cnt >= 2, f"cnt={cnt}")
# 写正向
rid = pm.save_relationship(DB, p1, p4, 'positive', 60, '喜欢一起', 'test')
rel = pm.get_pair_relationship(DB, p1, p4)
check('3b. 正向关系写入', rel is not None and rel['relationship_type'] == 'positive' and rel['relationship_score'] == 60)
# 写负向（avoid）
pm.save_relationship(DB, p1, p6, 'avoid', -100, '不喜欢', 'test')
rel2 = pm.get_pair_relationship(DB, p1, p6)
check('3c. 负向/avoid 关系写入', rel2 is not None and rel2['relationship_type'] == 'avoid')

# ===== 4. avoid 过滤 =====
# 重算局型，保证 p1 有竞技偏好
pm.compute_table_style(DB)
match = pm.match_table(DB, 'competitive', [p1], 3)
avoid_ids = [c['player_id'] for c in match['not_recommended'] if c['reason_type'] == 'avoid']
check('4. avoid 过滤 (p6 被强制排除)', p6 in avoid_ids, f"avoid名单={avoid_ids}")

# ===== 5. 推荐排序 =====
rec_sorted = match['recommended']
if len(rec_sorted) >= 2:
    ok = all(rec_sorted[i]['score'] >= rec_sorted[i+1]['score'] for i in range(len(rec_sorted)-1))
    check('5. 推荐按分数降序', ok)
else:
    check('5. 推荐排序 (候选不足，跳过)', True)

# ===== 6. 无数据情况 =====
# p5 无到店记录：主动性应为 unknown，且匹配不报错
check('6. 无数据主动性=unknown (p5)', m[p5]['initiative_level'] == 'unknown')
m2 = pm.match_table(DB, 'competitive', [p1], 5)
check('6b. 无数据玩家可参与匹配(不崩溃)', isinstance(m2, dict) and 'recommended' in m2)

# ===== 7. 人工备注优先级 =====
# p1,p4 有自动共同桌(正向建议)，但人工写入 avoid -> 应返回人工 avoid
pm.save_relationship(DB, p1, p4, 'avoid', -80, '人工覆盖：冲突', 'test')
rel3 = pm.get_pair_relationship(DB, p1, p4)
check('7. 人工备注优先于自动 (p1,p4=avoid)', rel3 is not None and rel3['relationship_type'] == 'avoid')
# 验证 analyze_relationships 不写 avoid（只返回建议）
before = DB.execute("SELECT COUNT(*) c FROM player_relationships WHERE relationship_type='avoid'").fetchone()['c']
sugs = pm.analyze_relationships(DB)
after = DB.execute("SELECT COUNT(*) c FROM player_relationships WHERE relationship_type='avoid'").fetchone()['c']
check('7b. 自动分析不写入 avoid', before == after and isinstance(sugs, list), f"before={before} after={after}")

# ===== 8. 任务生成 =====
# 先清除已有 table_match 任务
DB.execute("DELETE FROM operation_tasks WHERE task_type='table_match'")
DB.commit()
summary = pm.generate_table_match_tasks(DB, 'competitive', [p1], 2, operator='test')
tasks = DB.execute("SELECT * FROM operation_tasks WHERE task_type='table_match'").fetchall()
check('8. 组局任务生成', summary['created'] >= 1 and len(tasks) == summary['created'], f"summary={summary}")
check('8b. 任务类型=table_match', all(t['task_type'] == 'table_match' for t in tasks))
check('8c. 任务优先级=high', all(t['priority'] == 'high' for t in tasks))
# 去重：再次生成不应重复
summary2 = pm.generate_table_match_tasks(DB, 'competitive', [p1], 2, operator='test')
check('8d. 任务去重 (同日不重复)', summary2['skipped_existing'] >= 1, f"summary2={summary2}")

# 清理临时库
DB.close()
try:
    if os.path.exists(_tmp.name):
        os.remove(_tmp.name)
except OSError:
    pass

print('\n=== 全部测试通过 ===')
