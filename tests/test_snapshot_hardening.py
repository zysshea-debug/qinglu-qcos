"""QCOS Final Hardening 专项测试 —— 结账快照持久化 + 奖励金后端权威

覆盖（对应验收清单第十一章）：
  1.  preview → 服务重启 → checkout 仍然成功（证明快照不依赖 Python 进程内存）
  2.  snapshot token 只能使用一次（USED 后拒绝）
  3.  双击 checkout 不重复扣奖励金
  4.  双击 checkout 不重复扣会员储值
  5.  商品金额变化 → 快照失效
  6.  商品内容变化但总金额相同 → 快照失效（指纹校验，非仅比金额）
  7.  reward 余额减少到低于锁定抵扣额 → 快照失效
  8.  支付金额差 0.02 元仍被拒绝（容差未放松）
  9.  伪造 client fee=500 而 server fee≈20 → 奖励按 server fee 计算
  10. 篡改请求中的 session/player → 拒绝发放
  11. 同一 draw_id 重复 grant → 只发放一次
  12. 服务重启后 grant / snapshot 账务一致

运行方式：
  - pytest tests/test_snapshot_hardening.py
  - python tests/test_snapshot_hardening.py

全部使用临时数据库，绝不触碰生产 qcos.db。
"""
import os
import sys
import sqlite3
import tempfile
import threading
import pytest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config

# 强制使用临时数据库 + mock 支付，绝不碰生产 qcos.db / 真实支付通道
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.DB_PATH = _tmp.name  # 同步绑定，避免写进其他模块的临时库
models.init_db()
import app as APP
import payment as payment_module
from billing import calculate_reward

payment_module._PROVIDERS.clear()
# 脚本直跑模式默认 mock 支付通道；pytest 下由 _mock_provider fixture 每测试覆盖，效果一致。
config.PAYMENT['provider'] = 'mock'

client = APP.app.test_client()


@pytest.fixture(autouse=True, scope='function')
def _mock_provider(request, _isolate_db_per_test):
    """每测试使用 mock 支付，避免真实通道；配合 conftest 的 PAYMENT 复位。"""
    import config
    config.PAYMENT['provider'] = 'mock'
    yield


PASS = 0
FAIL = 0


def ok(name):
    global PASS
    PASS += 1
    print(f'  [OK] {name}')


def bad(name, msg):
    global FAIL
    FAIL += 1
    print(f'  [FAIL] {name}: {msg}')


def login():
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    assert r.status_code == 200, r.data


# 模块级登录：保证 pytest 收集后执行 test_* 时已通过鉴权
login()


def db():
    d = sqlite3.connect(config.DB_PATH)
    d.row_factory = sqlite3.Row
    return d


def reset_state():
    """关闭所有进行中台桌并释放机器，避免机器被占用导致开台失败。"""
    d = db()
    for s in d.execute("SELECT id FROM sessions WHERE status='active'").fetchall():
        d.execute("UPDATE sessions SET status='closed', end_time=? WHERE id=?",
                  [datetime.now().isoformat(), s['id']])
    d.execute("UPDATE machines SET status='idle' WHERE status='active'")
    d.commit()
    d.close()


def add_player(name):
    r = client.post('/api/players', json={'name': name, 'phone': '', 'wechat': ''})
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def add_member(player_id, balance, reward=0.0):
    r = client.post('/api/members', json={
        'player_id': player_id,
        'initial_balance': balance,
        'payment_method': 'cash'
    })
    assert r.status_code == 201, r.data
    mid = r.get_json()['id']
    if reward > 0:
        d = db()
        d.execute('UPDATE members SET reward_balance=? WHERE id=?', [reward, mid])
        d.commit()
        d.close()
    return mid


def start_session(machine_id, player_id, player_name, start_iso):
    r = client.post('/api/sessions', json={
        'machine_id': machine_id,
        'start_time': start_iso,
        'players': [{'name': player_name, 'player_id': player_id}]
    })
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    d = db()
    spid = d.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    d.close()
    return sid, spid


def preview(sid, spid):
    r = client.get(f'/api/sessions/{sid}/players/{spid}/preview')
    assert r.status_code == 200, r.data
    return r.get_json()


def checkout(sid, spid, token=None, **kw):
    body = {
        'payment_method': 'cash',
        'member_id': None,
        'product_total': 0,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0,
    }
    body.update(kw)
    if token is not None:
        body['checkout_token'] = token
    return client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json=body)


def micropay(amount, method='scan_wechat'):
    r = client.post('/api/payment/micropay', json={
        'auth_code': '134567890123456',
        'amount': amount,
        'method': method
    })
    assert r.status_code == 200, r.data
    mp = r.get_json()
    assert mp.get('status') == 'SUCCESS' and mp.get('out_trade_no'), mp
    return mp['out_trade_no']


def grant(member_id, reward_type, session_id=None, session_player_id=None, fee=None, draw_id=None):
    payload = {'member_id': member_id, 'reward_type': reward_type}
    if session_id is not None:
        payload['session_id'] = session_id
    if session_player_id is not None:
        payload['session_player_id'] = session_player_id
    if fee is not None:
        payload['fee'] = fee
    if draw_id is not None:
        payload['draw_id'] = draw_id
    return client.post('/api/rewards/grant', json=payload)


def member_row(member_id):
    d = db()
    m = d.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
    d.close()
    return dict(m) if m else None


def snap_row(token):
    d = db()
    r = d.execute('SELECT * FROM checkout_snapshots WHERE token=?', [token]).fetchone()
    d.close()
    return dict(r) if r else None


def restart_app():
    """模拟服务重启：丢弃 Flask app 及全部 Python 进程内状态后重新构建。"""
    global client
    import importlib
    importlib.reload(APP)
    client = APP.app.test_client()
    login()


# ============================================================
# 1. preview → 重启 → checkout 成功（快照不依赖进程内存）
# ============================================================
def test_1_preview_restart_checkout_success():
    print('\n[1] preview → 服务重启 → checkout 仍然成功')
    reset_state()
    start = (datetime.now() - timedelta(hours=3)).isoformat()
    pid = add_player('H1-重启')
    mid = add_member(pid, 500)
    sid, spid = start_session(1, pid, 'H1-重启', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']
    assert token, 'preview 应返回 checkout_token'
    assert snap_row(token)['status'] == 'ACTIVE'

    # 重启：进程内一切内存状态（含任何 dict 缓存）全部丢失
    restart_app()
    assert not hasattr(APP, 'CHECKOUT_SNAPSHOTS'), '生产代码不应再存在进程内存快照字典'

    # 重启后用原 token 结账：快照来自数据库，必须仍然可用
    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert abs(res['grand_total'] - grand) < 0.001, f"重启后金额应一致: {res['grand_total']} vs {grand}"
    assert snap_row(token)['status'] == 'USED'
    ok('重启后同一 token 结账成功且金额一致')


# ============================================================
# 2. snapshot token 只能使用一次
# ============================================================
def test_2_snapshot_token_single_use():
    print('\n[2] snapshot token 只能使用一次')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H2-单次')
    mid = add_member(pid, 500)
    sid, spid = start_session(2, pid, 'H2-单次', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    r1 = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r1.status_code == 200, r1.get_json()
    assert snap_row(token)['status'] == 'USED', '首次结账后快照应 USED'

    # 第二次同一 token：必须拒绝
    r2 = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r2.status_code in (400, 409), f"应拒绝重复使用: {r2.status_code} {r2.get_json()}"
    # 无论命中"已结账"还是 SNAPSHOT_ALREADY_USED，都不得产生第二次扣款
    bal_1 = member_row(mid)['balance']
    r3 = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r3.status_code in (400, 409)
    assert abs(member_row(mid)['balance'] - bal_1) < 0.001, '重复结账不得二次扣款'
    ok(f'token 仅可使用一次（第二次 {r2.status_code} 拒绝）')


# ============================================================
# 3 & 4. 双击 checkout 不重复扣奖励金 / 不重复扣储值
# ============================================================
def test_3_double_click_no_double_reward():
    print('\n[3] 双击 checkout 不重复扣奖励金')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H3-奖励')
    mid = add_member(pid, 500, reward=30.0)
    sid, spid = start_session(1, pid, 'H3-奖励', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    deduct = p['reward_deduct']
    assert deduct > 0, f"应存在奖励金抵扣: {p}"

    results = _concurrent_checkout(sid, spid, token, mid)
    assert results.count(200) == 1, f"应恰好一次成功: {results}"

    m = member_row(mid)
    assert abs(m['reward_balance'] - (30.0 - deduct)) < 0.001, f"奖励金只应扣一次: {m['reward_balance']}"
    d = db()
    red = d.execute('SELECT COUNT(*) c FROM reward_redemptions WHERE member_id=?', [mid]).fetchone()['c']
    d.close()
    assert red == 1, f"核销流水应只有一条, 实际 {red}"
    ok(f'双击：奖励金仅扣一次（-{-deduct}），核销流水 1 条')


def test_4_double_click_no_double_balance():
    print('\n[4] 双击 checkout 不重复扣储值')
    reset_state()
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('H4-储值')
    mid = add_member(pid, 300)
    sid, spid = start_session(1, pid, 'H4-储值', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']
    assert grand > 0

    results = _concurrent_checkout(sid, spid, token, mid)
    assert results.count(200) == 1, f"应恰好一次成功: {results}"

    m = member_row(mid)
    assert abs(m['balance'] - (300 - grand)) < 0.001, f"储值只应扣一次: {m['balance']} (grand={grand})"
    d = db()
    sp = d.execute('SELECT status FROM session_players WHERE id=?', [spid]).fetchone()['status']
    spent = d.execute('SELECT total_spent FROM members WHERE id=?', [mid]).fetchone()['total_spent']
    d.close()
    assert sp == 'checked_out'
    # 若发生重复扣款，total_spent 会被累加两次
    assert abs(spent - grand) < 0.001, f"累计消费应只记一次: {spent} vs {grand}"
    ok(f'双击：储值仅扣一次（-{grand}），累计消费 {spent}')


def _concurrent_checkout(sid, spid, token, member_id, n=2):
    """并发发起 n 次同一 token 的结账，返回各请求状态码列表。

    用 Barrier 尽量对齐双击时刻；无论是否真重叠，断言都成立：
    至多一个请求可以成功认领快照。
    """
    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(n)

    def worker():
        # 每个线程独立 client，避免共享 cookie/上下文
        c = APP.app.test_client()
        assert c.post('/api/auth/login',
                      json={'username': 'admin', 'password': 'admin123'}).status_code == 200
        body = {
            'payment_method': 'member', 'member_id': member_id,
            'product_total': 0, 'discount_id': None, 'start_time': None,
            'is_overnight': False, 'manual_discount_type': '',
            'manual_discount_value': 0, 'checkout_token': token,
        }
        barrier.wait()
        try:
            r = c.post(f'/api/sessions/{sid}/players/{spid}/checkout', json=body)
            code = r.status_code
        except Exception as e:  # sqlite 锁等异常视为未成功
            code = f'EXC:{e}'
        with lock:
            results.append(code)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


# ============================================================
# 5. 商品金额变化 → 快照失效
# ============================================================
def test_5_product_amount_change_invalidates_snapshot():
    print('\n[5] 商品金额变化 → 快照失效')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H5-商品额')
    mid = add_member(pid, 500)
    sid, spid = start_session(3, pid, 'H5-商品额', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    assert p['product_total'] == 0

    r = client.post('/api/products/sell', json={
        'session_id': sid, 'session_player_id': spid, 'product_id': 1, 'quantity': 2
    })
    assert r.status_code == 201, r.data

    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 400, r.get_json()
    assert '商品已变更' in (r.get_json().get('error') or ''), r.get_json()

    p2 = preview(sid, spid)
    assert p2['product_total'] > 0
    r2 = checkout(sid, spid, token=p2['checkout_token'], payment_method='member', member_id=mid)
    assert r2.status_code == 200, r2.get_json()
    ok('新增商品后旧快照失效，重新 preview 成功')


# ============================================================
# 6. 商品内容变化但总金额相同 → 快照失效（指纹校验）
# ============================================================
def test_6_same_amount_different_product_invalidates_snapshot():
    print('\n[6] 同价换品（总额不变）→ 快照失效')
    reset_state()
    d = db()
    prods = d.execute('SELECT id, name, price FROM products WHERE is_active=1 ORDER BY id LIMIT 2').fetchall()
    d.close()
    assert len(prods) >= 2, '需要至少两个商品以构造同价换品'
    pa, pb = dict(prods[0]), dict(prods[1])

    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H6-换品')
    mid = add_member(pid, 500)
    sid, spid = start_session(3, pid, 'H6-换品', start)

    # 卖 2 件 A
    r = client.post('/api/products/sell', json={
        'session_id': sid, 'session_player_id': spid, 'product_id': pa['id'], 'quantity': 2
    })
    assert r.status_code == 201, r.data
    p = preview(sid, spid)
    token = p['checkout_token']
    total_before = p['product_total']

    # 同总价换品：A×2(单价 pa.price) → B×1(单价=原总额)，product_total 不变但内容变化
    d = db()
    d.execute('UPDATE product_sales SET product_id=?, product_name=?, price=?, quantity=? '
              'WHERE session_player_id=?',
              [pb['id'], pb['name'], total_before, 1, spid])
    d.commit()
    total_after = d.execute('SELECT COALESCE(SUM(total),0) t FROM product_sales '
                            'WHERE session_player_id=?', [spid]).fetchone()['t']
    d.close()
    assert abs(total_after - total_before) < 0.001, '构造前提：换品后总金额必须相同'

    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 400, r.get_json()
    assert '商品已变更' in (r.get_json().get('error') or ''), r.get_json()
    ok(f'总额同为 {total_before} 但内容已变 → 指纹校验命中并拒绝')


# ============================================================
# 7. reward 余额减少 → 快照失效
# ============================================================
def test_7_reward_balance_drop_invalidates_snapshot():
    print('\n[7] reward 余额降到低于锁定抵扣额 → 快照失效')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H7-奖励变化')
    mid = add_member(pid, 500, reward=30.0)
    sid, spid = start_session(1, pid, 'H7-奖励变化', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    assert p['reward_deduct'] > 0

    # 模拟奖励金被其他渠道消耗到低于快照锁定额
    d = db()
    d.execute('UPDATE members SET reward_balance=? WHERE id=?', [p['reward_deduct'] - 10, mid])
    d.commit()
    d.close()

    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 400, r.get_json()
    assert '奖励金余额已变化' in (r.get_json().get('error') or ''), r.get_json()

    d = db()
    red = d.execute('SELECT COUNT(*) c FROM reward_redemptions WHERE member_id=?', [mid]).fetchone()['c']
    d.close()
    assert red == 0, '失效快照不得产生核销流水'
    ok('奖励金余额不足时旧快照失效且不产生核销')


# ============================================================
# 8. 支付金额差 0.02 → 仍拒绝
# ============================================================
def test_8_payment_diff_0_02_rejected():
    print('\n[8] 支付金额差 0.02 元仍拒绝（容差未放松）')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H8-容差')
    sid, spid = start_session(2, pid, 'H8-容差', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']

    ref = micropay(round(grand + 0.02, 2))
    r = checkout(sid, spid, token=token, payment_method='scan_wechat', payment_ref=ref)
    assert r.status_code == 400, r.get_json()
    assert '不一致' in (r.get_json().get('error') or ''), r.get_json()
    ok('差 0.02 元仍被门禁拒绝')


# ============================================================
# 9. 伪造 client fee=500，server fee≈20 → 按 server fee 发奖
# ============================================================
def test_9_forged_client_fee_ignored():
    print('\n[9] 伪造 client fee=500 → 后台仍按 server fee 发奖')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H9-伪造')
    mid = add_member(pid, 0)
    sid, spid = start_session(1, pid, 'H9-伪造', start)

    # 伪造高额台费：应被识别为 FEE_STALE 且绝不据其发奖
    r = grant(mid, 'free', session_id=sid, session_player_id=spid, fee=500)
    assert r.status_code == 409, f"应 FEE_STALE 拒绝: {r.status_code} {r.get_json()}"
    assert r.get_json().get('code') == 'FEE_STALE', r.get_json()
    stale = r.get_json()
    assert abs(stale['client_fee'] - 500) < 0.001
    assert stale['server_fee'] < 100, f"真实台费应远小于伪造值: {stale['server_fee']}"
    assert member_row(mid)['reward_balance'] == 0, 'FEE_STALE 不得发放任何奖励'

    # 正常发放：金额按后端 server_fee 计算，而非前端传入值
    r2 = grant(mid, 'free', session_id=sid, session_player_id=spid)
    assert r2.status_code == 201, r2.get_json()
    j = r2.get_json()
    server_fee = j['server_fee']
    expected = calculate_reward(server_fee, 'free')
    assert abs(j['reward_amount'] - expected) < 0.001, f"应按 server_fee 计算: {j}"
    assert abs(member_row(mid)['reward_balance'] - expected) < 0.001

    d = db()
    row = d.execute('SELECT * FROM reward_grants WHERE member_id=? ORDER BY id DESC LIMIT 1',
                    [mid]).fetchone()
    d.close()
    assert abs(row['original_fee'] - server_fee) < 0.001, '流水记录必须是后端台费'
    assert abs(row['original_fee'] - 500) > 1, '流水不得记录伪造的 client fee'
    ok(f'伪造 500 被拒(FEE_STALE)，实际按 server_fee={server_fee} 发放 {expected}')


# ============================================================
# 10. 篡改请求中的 session/player → 拒绝
# ============================================================
def test_10_forged_session_player_rejected():
    print('\n[10] 篡改 session/player → 拒绝发放')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()

    # 受害者：有会员、有进行中桌局
    pid_a = add_player('H10-A')
    mid_a = add_member(pid_a, 0)
    sid_a, spid_a = start_session(1, pid_a, 'H10-A', start)

    # 攻击者控制的另一桌
    pid_b = add_player('H10-B')
    add_member(pid_b, 0)
    sid_b, spid_b = start_session(2, pid_b, 'H10-B', start)

    # 1) 冒用他人 session_player 给自己会员发奖
    r = grant(mid_a, 'free', session_id=sid_b, session_player_id=spid_b)
    assert r.status_code == 400, f"应拒绝: {r.status_code} {r.get_json()}"
    assert member_row(mid_a)['reward_balance'] == 0

    # 2) 不存在的 session_player
    r = grant(mid_a, 'free', session_id=sid_a, session_player_id=999999)
    assert r.status_code == 400, f"应拒绝: {r.status_code} {r.get_json()}"

    # 3) session 与 player 不匹配（交叉组合）
    r = grant(mid_a, 'free', session_id=sid_a, session_player_id=spid_b)
    assert r.status_code == 400, f"应拒绝: {r.status_code} {r.get_json()}"

    assert member_row(mid_a)['reward_balance'] == 0, '任何伪造尝试都不得产生奖励'
    d = db()
    cnt = d.execute('SELECT COUNT(*) c FROM reward_grants WHERE member_id=?', [mid_a]).fetchone()['c']
    d.close()
    assert cnt == 0
    ok('伪造/错配 session 与 player 均被拒绝，无奖励产生')


# ============================================================
# 11. 同一 draw_id 重复 grant → 只发一次
# ============================================================
def test_11_draw_id_idempotent():
    print('\n[11] 同一 draw_id 重复 grant → 只发放一次')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H11-幂等')
    mid = add_member(pid, 0)
    sid, spid = start_session(1, pid, 'H11-幂等', start)

    r1 = grant(mid, 'free', session_id=sid, session_player_id=spid, draw_id='DRAW-2026-0001')
    assert r1.status_code == 201, r1.get_json()
    first = r1.get_json()
    amount = first['reward_amount']
    assert amount > 0
    bal_after_first = member_row(mid)['reward_balance']

    # 重复点击：同一 draw_id
    r2 = grant(mid, 'free', session_id=sid, session_player_id=spid, draw_id='DRAW-2026-0001')
    assert r2.status_code == 200, r2.get_json()
    second = r2.get_json()
    assert second.get('idempotent') is True, second
    assert second['grant_id'] == first['grant_id'], '应返回原发放结果'
    assert abs(second['reward_amount'] - amount) < 0.001

    # 余额与流水都不得变化
    assert abs(member_row(mid)['reward_balance'] - bal_after_first) < 0.001, '重复发放不得增加余额'
    d = db()
    cnt = d.execute("SELECT COUNT(*) c FROM reward_grants WHERE draw_id='DRAW-2026-0001'").fetchone()['c']
    d.close()
    assert cnt == 1, f"draw_id 应唯一: {cnt}"
    ok(f'同一 draw_id 重复请求只发一次（+{amount}）')


# ============================================================
# 12. 服务重启后 grant / snapshot 账务一致
# ============================================================
def test_12_restart_then_grant_and_checkout_consistent():
    print('\n[12] 重启后 grant 幂等与 snapshot 账务一致')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('H12-重启账务')
    mid = add_member(pid, 500)
    sid, spid = start_session(1, pid, 'H12-重启账务', start)

    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']

    restart_app()

    # 重启后发放（带 draw_id），再重复一次验证幂等
    g1 = grant(mid, 'free', session_id=sid, session_player_id=spid, draw_id='DRAW-RESTART-1')
    assert g1.status_code == 201, g1.get_json()
    reward_before_checkout = member_row(mid)['reward_balance']
    g2 = grant(mid, 'free', session_id=sid, session_player_id=spid, draw_id='DRAW-RESTART-1')
    assert g2.status_code == 200 and g2.get_json().get('idempotent') is True
    assert abs(member_row(mid)['reward_balance'] - reward_before_checkout) < 0.001

    # 重启后旧 token 仍可用，且金额与 preview 完全一致
    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 200, r.get_json()
    assert abs(r.get_json()['grand_total'] - grand) < 0.001

    # 账务一致：储值只扣一次，奖励金未被本次结账抵扣（快照冻结的是 preview 时刻的 reward_deduct）
    m = member_row(mid)
    assert abs(m['balance'] - (500 - grand)) < 0.001, f"储值应只扣一次: {m['balance']}"
    assert abs(m['reward_balance'] - reward_before_checkout) < 0.001, \
        f"快照冻结 reward_deduct={p['reward_deduct']}，不应额外核销: {m['reward_balance']}"
    assert snap_row(token)['status'] == 'USED'
    ok('重启后 grant 幂等 + 快照结账账务一致')


if __name__ == '__main__':
    login()
    for fn in (
        test_1_preview_restart_checkout_success,
        test_2_snapshot_token_single_use,
        test_3_double_click_no_double_reward,
        test_4_double_click_no_double_balance,
        test_5_product_amount_change_invalidates_snapshot,
        test_6_same_amount_different_product_invalidates_snapshot,
        test_7_reward_balance_drop_invalidates_snapshot,
        test_8_payment_diff_0_02_rejected,
        test_9_forged_client_fee_ignored,
        test_10_forged_session_player_rejected,
        test_11_draw_id_idempotent,
        test_12_restart_then_grant_and_checkout_consistent,
    ):
        try:
            fn()
        except AssertionError as e:
            bad(fn.__name__, str(e))
        except Exception as e:
            bad(fn.__name__, f'异常 {type(e).__name__}: {e}')
    print(f'\n汇总: PASS={PASS}, FAIL={FAIL}')
    sys.exit(0 if FAIL == 0 else 1)
