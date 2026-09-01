"""结账金额快照（Amount Snapshot）回归测试 —— pytest 原生 + 脚本双模式

覆盖：
  1. preview 生成结账快照 token（sp 级 /players/<sp_id>/preview）
  2. preview 后延迟 / 超长桌局：金额冻结，结账不因几秒漂移重新涨价（不 400）
  3. 支付确认金额差 0.02 元仍被门禁拒绝（容差未放松）
  4. preview 后新增商品：旧快照失效，必须重新 preview
  5. preview 后奖励金变化：旧快照失效 + 防同一快照重复核销
  6. reward_deduct=0 时行为不变（快照对无奖励金玩家中性）
  7. 有奖励金时快照含 reward_deduct 并正确抵扣、写流水
  8. 现金 / 会员 / 扫码 三种结账均走冻结快照
  9. 未确认支付直接结账仍被门禁拒绝
 10. 支付确认开关开启(mock)
 11. 旧逻辑（不带 token）结账仍可用，无回归

运行方式：
  - pytest tests/test_payment.py        （pytest 模式，逐个 test_* 收集）
  - python tests/test_payment.py        （脚本模式，__main__ 直跑，自汇总 PASS/FAIL）

使用临时数据库，绝不触碰生产 qcos.db。
"""
import os
import sys
import sqlite3
import tempfile
import pytest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config
# 强制使用临时数据库，绝不要碰生产 qcos.db
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.DB_PATH = _tmp.name  # 同步绑定，避免写进其他模块的临时库
models.init_db()
import app as APP
import payment as payment_module
payment_module._PROVIDERS.clear()

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
    """关闭所有进行中的台桌并把机器置为空闲，避免机器被占用导致开台失败。"""
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


# ============================================================
# 1. preview 生成结账快照 token
# ============================================================
def test_preview_returns_checkout_token():
    print('\n[1] preview 生成结账快照 token')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T1')
    sid, spid = start_session(1, pid, '快照-T1', start)
    p = preview(sid, spid)
    assert 'checkout_token' in p and p['checkout_token'], 'preview 应返回 checkout_token'
    # 无商品、无奖励金时 grand_total == final_fee
    assert p['product_total'] == 0
    assert p['grand_total'] == round(p['final_fee'] - (p.get('reward_deduct') or 0), 2)
    ok('preview 返回结账快照 token')


# ============================================================
# 2. preview 后延迟 / 超长桌局：金额冻结，不 400
# ============================================================
def test_snapshot_freezes_amount_long_session():
    print('\n[2] preview 后超长桌局金额冻结，结账不因时间漂移重新涨价(不 400)')
    reset_state()
    # 超长桌局：开台 10 小时（真实门店扫码延迟/长时间桌局均可能触发原漂移）
    start = (datetime.now() - timedelta(hours=10)).isoformat()
    pid = add_player('快照-T2')
    sid, spid = start_session(1, pid, '快照-T2', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']
    assert token and grand > 0

    # 直接用冻结快照结账（模拟预览后几秒才扫码完成）
    r = checkout(sid, spid, token=token, payment_method='cash')
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    # 结账金额必须等于预览冻结金额（未被 end_time=now 实时重算涨价）
    assert res['grand_total'] == grand, f"冻结金额应==preview: {res['grand_total']} vs {grand}"
    assert res['fee'] == p['fee'], '台费应取自冻结快照而非实时重算'
    assert res['final_fee'] == p['final_fee']
    ok('超长桌局结账金额冻结不 400')


# ============================================================
# 3. 支付确认金额差 0.02 元仍被门禁拒绝
# ============================================================
def test_payment_diff_0_02_rejected():
    print('\n[3] 支付确认金额差 0.02 仍拒绝(容差未放松)')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T3')
    sid, spid = start_session(2, pid, '快照-T3', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']

    ref = micropay(grand + 0.02)  # 多付 2 分
    r = checkout(sid, spid, token=token, payment_method='scan_wechat', payment_ref=ref)
    assert r.status_code == 400, r.get_json()
    assert '不一致' in (r.get_json().get('error') or ''), r.get_json()
    ok('差0.02仍拒绝')


# ============================================================
# 4. preview 后新增商品：旧快照失效
# ============================================================
def test_product_change_invalidates_snapshot():
    print('\n[4] preview 后新增商品使旧快照失效')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T4')
    mid = add_member(pid, 200)
    sid, spid = start_session(3, pid, '快照-T4', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    assert token and p['product_total'] == 0

    # preview 后新增商品
    r = client.post('/api/products/sell', json={
        'session_id': sid, 'session_player_id': spid, 'product_id': 1, 'quantity': 2
    })
    assert r.status_code == 201, r.data

    # 用旧 token 结账 -> 应 400 商品已变更（禁止 旧台费 + 新商品 混用同一笔支付确认）
    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 400, r.get_json()
    assert '商品已变更' in (r.get_json().get('error') or ''), r.get_json()

    # 重新 preview 得新 token，结账成功并含商品
    p2 = preview(sid, spid)
    token2 = p2['checkout_token']
    assert p2['product_total'] > 0
    r2 = checkout(sid, spid, token=token2, payment_method='member', member_id=mid)
    assert r2.status_code == 200, r2.get_json()
    assert r2.get_json()['product_total'] == p2['product_total']
    ok('商品变更旧快照失效，重新preview成功')


# ============================================================
# 5. preview 后奖励金变化：旧快照失效 + 防重复核销
# ============================================================
def test_reward_snapshot_no_double_redeem():
    print('\n[5] 奖励金快照：防重复核销 + 余额变化失效')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T5')
    mid = add_member(pid, 100, reward=30.0)
    sid, spid = start_session(1, pid, '快照-T5', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    assert p.get('reward_deduct', 0) > 0, f"应有奖励金抵扣，实际{p.get('reward_deduct')}"
    reward_before = p['reward_deduct']

    # 第一次结账（现金）：成功并扣奖励金
    r = checkout(sid, spid, token=token, payment_method='cash')
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert res['reward_deduct'] == reward_before

    d = db()
    rb = d.execute('SELECT reward_balance FROM members WHERE id=?', [mid]).fetchone()['reward_balance']
    red = d.execute('SELECT COUNT(*) c FROM reward_redemptions WHERE member_id=? AND session_player_id=?',
                    [mid, spid]).fetchone()['c']
    d.close()
    assert abs(rb - (30.0 - reward_before)) < 0.001, f"奖励金应扣减至 {30 - reward_before}, 实际 {rb}"
    assert red == 1, '应恰好写一条奖励金核销流水'

    # 防重复核销：结账成功后快照在数据库中被标记 USED，且奖励金仅核销一次
    d = db()
    snap_row = d.execute('SELECT status, used_at FROM checkout_snapshots WHERE token=?', [token]).fetchone()
    d.close()
    assert snap_row is not None, '快照应持久化在 checkout_snapshots 表中'
    assert snap_row['status'] == 'USED', f"结账后快照应标记 USED 防重复核销，实际 {snap_row['status']}"
    assert snap_row['used_at'], 'USED 快照应记录 used_at'
    # 即便再次用同一 token 结账，也必须被拒绝，且奖励金不再重复核销
    r2 = checkout(sid, spid, token=token, payment_method='cash')
    assert r2.status_code == 400, r2.get_json()
    d = db()
    rb2 = d.execute('SELECT reward_balance FROM members WHERE id=?', [mid]).fetchone()['reward_balance']
    red2 = d.execute('SELECT COUNT(*) c FROM reward_redemptions WHERE member_id=? AND session_player_id=?',
                     [mid, spid]).fetchone()['c']
    d.close()
    assert abs(rb2 - (30.0 - reward_before)) < 0.001, f"奖励金不应被二次核销: {rb2}"
    assert red2 == 1, '奖励金核销流水应只有一条'

    # 新开一局：验证奖励金余额被消耗到低于快照锁定额 -> 旧快照失效
    reset_state()
    start2 = (datetime.now() - timedelta(hours=1)).isoformat()
    pid2 = add_player('快照-T5b')
    mid2 = add_member(pid2, 100, reward=30.0)
    sid2, spid2 = start_session(1, pid2, '快照-T5b', start2)
    p2 = preview(sid2, spid2)
    token2 = p2['checkout_token']
    assert p2.get('reward_deduct', 0) > 0
    # 模拟奖励金被其他渠道消耗到低于快照锁定的 reward_deduct
    d = db()
    d.execute('UPDATE members SET reward_balance=? WHERE id=?', [p2['reward_deduct'] - 10, mid2])
    d.commit()
    d.close()
    r3 = checkout(sid2, spid2, token=token2, payment_method='cash')
    assert r3.status_code == 400, r3.get_json()
    assert '奖励金余额已变化' in (r3.get_json().get('error') or ''), r3.get_json()
    ok('奖励金防重复核销 + 余额变化失效')


# ============================================================
# 6. reward_deduct=0 时行为不变
# ============================================================
def test_reward_deduct_zero_unchanged():
    print('\n[6] reward_deduct=0 时行为不变')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T6')
    mid = add_member(pid, 100, reward=0.0)
    sid, spid = start_session(1, pid, '快照-T6', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    assert p.get('reward_deduct', 0) == 0, 'reward_deduct 应为 0'
    assert p['grand_total'] == p['final_fee'], '无奖励金时 grand_total==final_fee'
    r = checkout(sid, spid, token=token, payment_method='cash')
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['reward_deduct'] == 0
    ok('reward_deduct=0 行为不变')


# ============================================================
# 7. 有奖励金时快照含 reward_deduct 并正确抵扣
# ============================================================
def test_reward_snapshot_includes_deduct():
    print('\n[7] 有奖励金时快照含 reward_deduct 并正确抵扣')
    reset_state()
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('快照-T7')
    mid = add_member(pid, 100, reward=50.0)
    sid, spid = start_session(1, pid, '快照-T7', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    assert p.get('reward_deduct', 0) > 0
    assert p['grand_total'] == round(p['final_fee'] - p['reward_deduct'], 2), 'grand_total 应扣除奖励金'

    r = checkout(sid, spid, token=token, payment_method='cash')
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert res['reward_deduct'] == p['reward_deduct']
    d = db()
    rb = d.execute('SELECT reward_balance FROM members WHERE id=?', [mid]).fetchone()['reward_balance']
    red = d.execute('SELECT COUNT(*) c FROM reward_redemptions WHERE member_id=? AND session_player_id=?',
                    [mid, spid]).fetchone()['c']
    d.close()
    assert abs(rb - (50.0 - p['reward_deduct'])) < 0.001
    assert red == 1, '应写一条奖励金核销流水'
    ok('奖励金快照含 reward_deduct 并抵扣')


# ============================================================
# 8. 现金 / 会员 / 扫码 三种结账均走冻结快照
# ============================================================
def test_cash_member_scan_with_snapshot():
    print('\n[8] 现金/会员/扫码 结账均走快照')
    # 现金
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T8a')
    add_member(pid, 500)
    sid, spid = start_session(1, pid, '快照-T8a', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    r = checkout(sid, spid, token=token, payment_method='cash')
    assert r.status_code == 200, r.get_json()
    ok('现金结账走快照')

    # 会员
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T8b')
    mid = add_member(pid, 500)
    sid, spid = start_session(1, pid, '快照-T8b', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']
    r = checkout(sid, spid, token=token, payment_method='member', member_id=mid)
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert res['member_balance_after'] == 500 - grand, f"{res['member_balance_after']} vs {500 - grand}"
    ok('会员结账走快照扣余额正确')

    # 扫码
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T8c')
    sid, spid = start_session(1, pid, '快照-T8c', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    grand = p['grand_total']
    ref = micropay(grand)
    r = checkout(sid, spid, token=token, payment_method='scan_wechat', payment_ref=ref)
    assert r.status_code == 200, r.get_json()
    ok('扫码结账走快照匹配支付金额')


# ============================================================
# 9. 未确认支付直接结账仍被门禁拒绝
# ============================================================
def test_unconfirmed_payment_rejected():
    print('\n[9] 未确认支付直接结账被拒(门禁仍生效)')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T9')
    sid, spid = start_session(1, pid, '快照-T9', start)
    p = preview(sid, spid)
    token = p['checkout_token']
    r = checkout(sid, spid, token=token, payment_method='scan_wechat')  # 无 payment_ref
    assert r.status_code == 400, r.get_json()
    assert '未确认' in (r.get_json().get('error') or ''), r.get_json()
    ok('未确认支付被拒')


# ============================================================
# 10. 支付确认开关开启(mock)
# ============================================================
def test_payment_status_enabled():
    print('\n[10] 支付确认开关开启(mock)')
    r = client.get('/api/payment/status')
    d = r.get_json()
    assert r.status_code == 200 and d.get('enabled') is True and d.get('provider') == 'mock', d
    ok('支付确认开关开启(mock)')


# ============================================================
# 11. 旧逻辑（不带 token）结账仍可用，无回归
# ============================================================
def test_legacy_checkout_without_token():
    print('\n[11] 无快照(旧逻辑)结账仍可用')
    reset_state()
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('快照-T11')
    mid = add_member(pid, 500)
    sid, spid = start_session(1, pid, '快照-T11', start)
    # 不传 checkout_token，走原有实时重算路径
    r = checkout(sid, spid, payment_method='member', member_id=mid)
    assert r.status_code == 200, r.get_json()
    ok('旧逻辑无token结账成功')


if __name__ == '__main__':
    tests = [
        test_preview_returns_checkout_token,
        test_snapshot_freezes_amount_long_session,
        test_payment_diff_0_02_rejected,
        test_product_change_invalidates_snapshot,
        test_reward_snapshot_no_double_redeem,
        test_reward_deduct_zero_unchanged,
        test_reward_snapshot_includes_deduct,
        test_cash_member_scan_with_snapshot,
        test_unconfirmed_payment_rejected,
        test_payment_status_enabled,
        test_legacy_checkout_without_token,
    ]
    for t in tests:
        try:
            t()
        except AssertionError as e:
            bad(t.__name__, str(e))
        except Exception as e:  # noqa: BLE001
            bad(t.__name__, f'异常: {type(e).__name__}: {e}')
    print(f'\n结果: {PASS} 通过, {FAIL} 失败')
    sys.exit(1 if FAIL else 0)
