"""玩家归档 + 条件式永久删除测试

覆盖：
- 归档：active->archived、archived_at 写入、默认列表排除、可筛选查到、可恢复
- 联动排除：不进入组局候选、不进入运营任务、不进入活跃统计、不进搜索建议
- 永久删除：有历史引用（场次/消费/会员/关系）禁止删除（409 PLAYER_HAS_HISTORY）；
  纯误录空档案可删除；staff 禁止删除、admin 可删除
- 数据保留：归档不删历史场次、不删会员余额

全部使用临时数据库，不触碰真实 qcos.db / .env。
"""
import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 测试专用环境变量（import config 之前设置，非生产凭据）
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_archive_delete')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_archive')

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.init_db()
import app as APP
import player_matching as pm
import operations as ops

client = APP.app.test_client()

PASS = 0
FAIL = 0


def ok(name):
    global PASS
    PASS += 1
    print(f'  [PASS] {name}')


def bad(name, msg):
    global FAIL
    FAIL += 1
    print(f'  [FAIL] {name}: {msg}')


def login(username='admin', password=None):
    if password is None:
        password = os.environ['QCOS_ADMIN_PASSWORD']
    r = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert r.status_code == 200, r.data
    return r


def logout():
    client.post('/api/auth/logout')


def db():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def add_player(name):
    r = client.post('/api/players', json={'name': name})
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def get_player(pid):
    c = db()
    row = c.execute('SELECT * FROM players WHERE id=?', [pid]).fetchone()
    c.close()
    return dict(row) if row else None


def add_member(player_id, balance):
    r = client.post('/api/members', json={
        'player_id': player_id,
        'initial_balance': balance,
        'payment_method': 'cash'
    })
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def close_all_sessions():
    c = db()
    c.execute("UPDATE sessions SET status='closed', end_time=? WHERE status='active'", [datetime.now().isoformat()])
    c.execute("UPDATE session_players SET status='checked_out' WHERE status='playing'")
    c.execute("UPDATE machines SET status='idle' WHERE status!='idle'")
    c.commit()
    c.close()


def start_session(machine_id, player_id, player_name, start_time):
    close_all_sessions()
    r = client.post('/api/sessions', json={
        'machine_id': machine_id,
        'start_time': start_time,
        'players': [{'name': player_name, 'player_id': player_id}]
    })
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def archive(pid, reason='离开成都'):
    return client.post(f'/api/players/{pid}/archive', json={'reason': reason})


def restore(pid):
    return client.post(f'/api/players/{pid}/restore')


def pdelete(pid):
    return client.delete(f'/api/players/{pid}')


def players_with_status(status='active'):
    r = client.get(f'/api/players?status={status}')
    assert r.status_code == 200, r.data
    return r.get_json()


# ===================== 1. 归档基础 =====================

def test_archive_basic():
    print('\n[1] 归档基础流程')
    login()
    pid = add_player('归档甲')
    r = archive(pid, '离开成都')
    if r.status_code == 200:
        ok('active 玩家可以归档 (200)')
    else:
        bad('active 玩家可以归档', r.data)
        return
    p = get_player(pid)
    if p and p['status'] == 'archived':
        ok('归档后 status=archived')
    else:
        bad('归档后 status', p)
    if p and p.get('archived_at'):
        ok('archived_at 已写入')
    else:
        bad('archived_at 写入', p)
    if p and p.get('archive_reason') == '离开成都':
        ok('archive_reason 已写入')
    else:
        bad('archive_reason 写入', p)
    # 默认列表（活跃）不含
    lst = players_with_status('active')
    if all(x['id'] != pid for x in lst):
        ok('archived 默认不出现在活跃列表')
    else:
        bad('archived 默认不出现在活跃列表', '仍出现在 active 列表')
    # status=all 含
    lst_all = players_with_status('all')
    if any(x['id'] == pid for x in lst_all):
        ok('status=all 可查到已归档玩家')
    else:
        bad('status=all 可查到已归档玩家', 'all 列表缺失')
    # status=archived 可查到
    lst_arch = players_with_status('archived')
    if any(x['id'] == pid for x in lst_arch):
        ok('archived 可通过状态筛选查到')
    else:
        bad('archived 可通过状态筛选查到', 'archived 列表缺失')
    # 搜索建议排除
    r = client.get(f'/api/players/search?name=归档甲')
    if r.status_code == 200 and all(x['id'] != pid for x in r.get_json()):
        ok('搜索建议排除已归档玩家')
    else:
        bad('搜索建议排除已归档玩家', r.data)
    # 恢复
    r2 = restore(pid)
    p2 = get_player(pid)
    if r2.status_code == 200 and p2 and p2['status'] == 'active' and p2.get('archived_at') is None:
        ok('archived 可以恢复 active 且 archived_at 清空')
    else:
        bad('archived 可以恢复 active', f'status={p2 and p2["status"]} archived_at={p2 and p2.get("archived_at")}')


# ===================== 2. 联动排除 =====================

def test_linkage_exclusion():
    print('\n[2] 归档玩家联动排除（组局/运营/统计）')
    login()
    # 活跃玩家 A 与 已归档玩家 B
    pid_a = add_player('联动活跃A')
    pid_b = add_player('联动归档B')
    archive(pid_b, '不再玩日麻')

    # 2a. 不进入组局候选
    c = db()
    res = pm.match_table(c, 'competitive', [], 4)
    c.close()
    all_ids = [x['player_id'] for x in res['recommended']] + [x['player_id'] for x in res['not_recommended']]
    if pid_b not in all_ids:
        ok('archived 不进入组局候选')
    else:
        bad('archived 不进入组局候选', '出现在 match_table 结果')
    if any(x['player_id'] == pid_a for x in res['recommended']):
        ok('活跃玩家仍在组局候选')
    else:
        bad('活跃玩家仍在组局候选', 'active 玩家不在 recommended')

    # 2b. 不进入运营任务（归档 B 不应产生任何任务）
    c = db()
    now = datetime.now()
    for pid in (pid_a, pid_b):
        c.execute(
            "UPDATE players SET customer_level='A', last_visit=?, total_visits=0 WHERE id=?",
            [(now - timedelta(days=30)).isoformat(), pid]
        )
    c.commit()
    summary = ops.generate_operation_tasks(c)
    c.close()
    c = db()
    cnt_a = c.execute('SELECT COUNT(*) n FROM operation_tasks WHERE player_id=?', [pid_a]).fetchone()['n']
    cnt_b = c.execute('SELECT COUNT(*) n FROM operation_tasks WHERE player_id=?', [pid_b]).fetchone()['n']
    c.close()
    if cnt_b == 0:
        ok('archived 不进入运营任务（召回/维护）')
    else:
        bad('archived 不进入运营任务', f'archived 生成了 {cnt_b} 个任务')
    if cnt_a >= 1:
        ok('活跃玩家正常生成运营任务')
    else:
        bad('活跃玩家正常生成运营任务', f'active 任务数 {cnt_a}')

    # 2c. 统计口径：归档不计入活跃指标
    r = client.get('/api/players/stats')
    s = r.get_json()
    active_ids = [p['id'] for p in players_with_status('all')]
    # 统计 active 计数应等于 status=active 的列表长度
    if s['total_active'] == len([p for p in players_with_status('active')]):
        ok('stats.total_active 与活跃列表一致')
    else:
        bad('stats.total_active', f"stats={s['total_active']} list={len([p for p in players_with_status('active')])}")
    if s['total_archived'] >= 1:
        ok('stats.total_archived 统计已归档数')
    else:
        bad('stats.total_archived', str(s))


# ===================== 3. 数据保留 =====================

def test_archive_preserves_history():
    print('\n[3] 归档保留历史数据（场次/会员余额）')
    login()
    # 历史场次
    pid = add_player('历史场次玩家')
    sid = start_session(1, pid, '历史场次玩家', (datetime.now() - timedelta(hours=2)).isoformat())
    # 会员余额
    mid = add_member(pid, 100.0)
    archive(pid, '离开成都')
    c = db()
    sp_cnt = c.execute('SELECT COUNT(*) n FROM session_players WHERE player_id=?', [pid]).fetchone()['n']
    bal = c.execute('SELECT balance FROM members WHERE id=?', [mid]).fetchone()
    c.close()
    if sp_cnt >= 1:
        ok('归档不删除历史场次')
    else:
        bad('归档不删除历史场次', 'session_players 被清空')
    if bal and abs(bal['balance'] - 100.0) < 0.001:
        ok('归档不删除会员余额')
    else:
        bad('归档不删除会员余额', f'balance={bal and bal["balance"]}')


# ===================== 4. 永久删除 =====================

def test_delete_blocked_by_history():
    print('\n[4] 永久删除：有历史引用则禁止')
    login()

    # 4a. 有历史场次
    pid = add_player('删-场次')
    start_session(1, pid, '删-场次', (datetime.now() - timedelta(hours=1)).isoformat())
    r = pdelete(pid)
    if r.status_code == 409 and r.get_json().get('error') == 'PLAYER_HAS_HISTORY':
        ok('有历史场次玩家禁止永久删除 (409 PLAYER_HAS_HISTORY)')
    else:
        bad('有历史场次玩家禁止永久删除', f'{r.status_code} {r.data}')

    # 4b. 有消费记录（product_sales）
    pid2 = add_player('删-消费')
    c = db()
    c.execute(
        "INSERT INTO product_sales (session_id, session_player_id, player_id, product_name, price, quantity, total, status, created_at) "
        "VALUES (NULL, NULL, ?, '可乐', 5, 1, 5, 'SETTLED', ?)",
        [pid2, datetime.now().isoformat()]
    )
    c.commit()
    c.close()
    r = pdelete(pid2)
    if r.status_code == 409 and r.get_json().get('error') == 'PLAYER_HAS_HISTORY':
        ok('有消费记录玩家禁止永久删除 (409)')
    else:
        bad('有消费记录玩家禁止永久删除', f'{r.status_code} {r.data}')

    # 4c. 有会员关联
    pid3 = add_player('删-会员')
    add_member(pid3, 50.0)
    r = pdelete(pid3)
    if r.status_code == 409 and r.get_json().get('error') == 'PLAYER_HAS_HISTORY':
        ok('有会员关联玩家禁止永久删除 (409)')
    else:
        bad('有会员关联玩家禁止永久删除', f'{r.status_code} {r.data}')

    # 4d. 有关系数据
    pid4 = add_player('删-关系A')
    pid5 = add_player('删-关系B')
    c = db()
    c.execute(
        "INSERT INTO player_relationships (player_a_id, player_b_id, relationship_type, relationship_score, source, created_at) "
        "VALUES (?, ?, 'neutral', 0, 'manual', ?)",
        [pid4, pid5, datetime.now().isoformat()]
    )
    c.commit()
    c.close()
    r = pdelete(pid4)
    if r.status_code == 409 and r.get_json().get('error') == 'PLAYER_HAS_HISTORY':
        ok('有关系数据玩家禁止永久删除 (409)')
    else:
        bad('有关系数据玩家禁止永久删除', f'{r.status_code} {r.data}')

    # 引用信息应返回表名
    if r.status_code == 409 and any(x.get('table') == 'player_relationships' for x in r.get_json().get('refs', [])):
        ok('409 响应包含引用表名')
    else:
        bad('409 响应包含引用表名', r.data)


def test_delete_empty_player():
    print('\n[5] 永久删除：纯误录空档案')
    login()
    pid = add_player('纯误录')
    r = pdelete(pid)
    if r.status_code == 200:
        ok('无任何历史的纯误录玩家可永久删除 (200)')
    else:
        bad('无任何历史的纯误录玩家可永久删除', f'{r.status_code} {r.data}')
        return
    if get_player(pid) is None:
        ok('删除后档案不存在')
    else:
        bad('删除后档案不存在', '玩家仍存在')
    r2 = client.get(f'/api/players/{pid}/detail')
    if r2.status_code == 404:
        ok('删除后 detail 返回 404')
    else:
        bad('删除后 detail 返回 404', f'{r2.status_code}')


def test_delete_staff_forbidden():
    print('\n[6] 永久删除：staff 禁止')
    login()
    # 创建 staff 用户
    r = client.post('/api/users', json={
        'username': 'staff_archive',
        'password': 'staff_pass_123',
        'name': '测试店员',
        'role': 'staff'
    })
    assert r.status_code == 201, r.data
    pid = add_player('店员要删的空档案')
    logout()
    login('staff_archive', 'staff_pass_123')
    r = pdelete(pid)
    if r.status_code == 403:
        ok('staff 不能永久删除 (403)')
    else:
        bad('staff 不能永久删除', f'{r.status_code} {r.data}')
    # 归档权限 staff 应该还有（玩家档案页可操作）
    r2 = archive(pid, '其他')
    if r2.status_code == 200:
        ok('staff 仍可归档玩家')
    else:
        bad('staff 仍可归档玩家', f'{r2.status_code} {r2.data}')
    # 但 staff 不能恢复已归档？恢复属玩家页操作，允许
    r3 = restore(pid)
    if r3.status_code == 200:
        ok('staff 可恢复已归档玩家')
    else:
        bad('staff 可恢复已归档玩家', f'{r3.status_code} {r3.data}')
    logout()
    login()
    # admin 补删该空档案（此时无任何引用）
    r4 = pdelete(pid)
    if r4.status_code == 200:
        ok('admin 可以删除符合条件的空档案')
    else:
        bad('admin 可以删除符合条件的空档案', f'{r4.status_code} {r4.data}')


# ===================== 5. 其他联动 =====================

def test_archived_in_member_and_restore_flow():
    print('\n[7] 归档玩家与会员独立 + 完整恢复')
    login()
    pid = add_player('会员归档恢复')
    add_member(pid, 200.0)
    r = archive(pid, '重复档案')
    if r.status_code != 200:
        bad('会员玩家可归档', r.data)
        return
    c = db()
    m = c.execute('SELECT balance, status FROM members WHERE player_id=?', [pid]).fetchone()
    c.close()
    if m and m['status'] == 'active' and abs(m['balance'] - 200.0) < 0.001:
        ok('归档不影响会员独立状态（会员仍 active，余额保留）')
    else:
        bad('归档不影响会员独立状态', f'{dict(m) if m else None}')
    r2 = restore(pid)
    p2 = get_player(pid)
    if r2.status_code == 200 and p2 and p2['status'] == 'active' and p2.get('archived_at') is None and p2.get('archive_reason') is None:
        ok('恢复后 status=active 且归档字段清空')
    else:
        bad('恢复后 status=active 且归档字段清空', str(p2))
    # 恢复后重新进入活跃列表
    lst = players_with_status('active')
    if any(x['id'] == pid for x in lst):
        ok('恢复后重新出现在活跃列表')
    else:
        bad('恢复后重新出现在活跃列表', '未出现')


def test_archive_twice_and_reason():
    print('\n[8] 重复归档/未归档恢复的边界')
    login()
    pid = add_player('边界玩家')
    r1 = archive(pid, '不再玩日麻')
    r2 = archive(pid, '其他')
    if r1.status_code == 200 and r2.status_code == 400:
        ok('重复归档返回 400')
    else:
        bad('重复归档返回 400', f'{r1.status_code} / {r2.status_code}')
    r3 = restore(pid)
    r4 = restore(pid)
    if r3.status_code == 200 and r4.status_code == 400:
        ok('未归档恢复返回 400')
    else:
        bad('未归档恢复返回 400', f'{r3.status_code} / {r4.status_code}')
    # 清理
    r5 = pdelete(pid)
    if r5.status_code == 200:
        ok('边界玩家空档案可删除')
    else:
        bad('边界玩家空档案可删除', f'{r5.status_code} {r5.data}')


# ===================== 执行 =====================

def main():
    test_archive_basic()
    test_linkage_exclusion()
    test_archive_preserves_history()
    test_delete_blocked_by_history()
    test_delete_empty_player()
    test_delete_staff_forbidden()
    test_archived_in_member_and_restore_flow()
    test_archive_twice_and_reason()
    print(f'\n===== 结果: PASS={PASS} FAIL={FAIL} =====')
    if FAIL:
        sys.exit(1)


if __name__ == '__main__':
    main()
