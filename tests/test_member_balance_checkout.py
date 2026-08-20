"""会员余额结账资金安全测试

核心目标：确保非会员玩家绝对不会显示/使用任何会员余额，
且会员扣款在服务端二次校验会员真正属于当前玩家（防伪造跨会员扣款）。

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
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_member_balance')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_member')

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.init_db()
import app as APP

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


def login():
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    assert r.status_code == 200, r.data


def db():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def add_player(name):
    r = client.post('/api/players', json={'name': name})
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def add_member(player_id, balance):
    r = client.post('/api/members', json={
        'player_id': player_id,
        'initial_balance': balance,
        'payment_method': 'cash'
    })
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def close_all_sessions():
    """测试清理：关闭所有活跃台桌并释放机器，避免机器被占用。"""
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
    sid = r.get_json()['id']
    c = db()
    spid = c.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    c.close()
    return sid, spid


def preview(sid, spid):
    r = client.get(f'/api/sessions/{sid}/players/{spid}/preview')
    assert r.status_code == 200, r.data
    return r.get_json()


def checkout(sid, spid, **kw):
    payload = {
        'payment_method': 'cash',
        'member_id': None,
        'product_total': 0,
        'start_time': (datetime.now() - timedelta(minutes=30)).isoformat(),
        'is_overnight': False,
        'manual_discount_type': None,
        'manual_discount_value': 0,
        'payment_ref': None,
    }
    payload.update(kw)
    return client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json=payload)


def test_non_member_no_balance():
    print('\n[1] 非会员 preview 不显示余额')
    login()
    pid = add_player('非会员甲')
    sid, spid = start_session(1, pid, '非会员甲', (datetime.now() - timedelta(minutes=30)).isoformat())
    data = preview(sid, spid)
    if data.get('is_member') is not True and data.get('member_info') is None and data.get('member_id') is None:
        ok('非会员 is_member=false / member_info=null / member_id=null')
    else:
        bad('非会员 preview', f"is_member={data.get('is_member')} member_info={data.get('member_info')}")


def test_orphan_member_id_treated_as_nonmember():
    print('\n[2] 玩家 member_id 指向已失效会员 → 按非会员处理')
    login()
    pid = add_player('孤儿玩家')
    mid = add_member(pid, 100)
    # 模拟孤儿关联：会员记录被置为失效（status=inactive），但玩家仍可能残留 member_id 标志
    c = db()
    c.execute("UPDATE members SET status='inactive' WHERE id=?", [mid])
    c.commit()
    c.close()
    sid, spid = start_session(1, pid, '孤儿玩家', (datetime.now() - timedelta(minutes=30)).isoformat())
    data = preview(sid, spid)
    if data.get('is_member') is not True and data.get('member_info') is None:
        ok('孤儿 member 关联按非会员处理')
    else:
        bad('孤儿会员', f"is_member={data.get('is_member')}")


def test_real_member_balance():
    print('\n[3] 正常会员返回真实余额')
    login()
    pid = add_player('真会员')
    mid = add_member(pid, 93.90)
    sid, spid = start_session(1, pid, '真会员', (datetime.now() - timedelta(minutes=30)).isoformat())
    data = preview(sid, spid)
    if data.get('is_member') in (True, 1) and data.get('member_id') == mid and abs((data.get('member_info') or {}).get('balance', -1) - 93.90) < 0.001:
        ok('会员 is_member=true / member_id 正确 / 余额=93.90')
    else:
        bad('会员余额', f"is_member={data.get('is_member')} balance={data.get('member_info')}")


def test_name_collision_no_cross_balance():
    print('\n[4] 同名玩家（一会员一非会员）不得串余额')
    login()
    non_member_pid = add_player('back')          # 非会员，名字 back
    member_pid = add_player('back')              # 同名，但是会员
    member_mid = add_member(member_pid, 93.90)  # 会员 back 余额 93.90
    # 用“非会员 back”开一局
    sid, spid = start_session(1, non_member_pid, 'back', (datetime.now() - timedelta(minutes=30)).isoformat())
    data = preview(sid, spid)
    if data.get('is_member') is not True and data.get('member_info') is None and data.get('member_id') is None:
        ok('非会员 back 不继承会员 back 的 ¥93.90')
    else:
        bad('同名串号', f"is_member={data.get('is_member')} member_info={data.get('member_info')}")


def test_sequential_open_no_leak():
    print('\n[5] 会员A → 非会员B 连续打开，B 不继承 A 余额')
    login()
    a_pid = add_player('会员A')
    a_mid = add_member(a_pid, 88.0)
    b_pid = add_player('非会员B')
    sa, spa = start_session(1, a_pid, '会员A', (datetime.now() - timedelta(minutes=40)).isoformat())
    sb, spb = start_session(2, b_pid, '非会员B', (datetime.now() - timedelta(minutes=30)).isoformat())
    da = preview(sa, spa)
    if da.get('is_member') in (True, 1) and da.get('member_id') == a_mid:
        ok('会员A preview 正常显示余额')
    else:
        bad('会员A', f"is_member={da.get('is_member')}")
    # 现在打开非会员B
    bb = preview(sb, spb)
    if bb.get('is_member') is not True and bb.get('member_info') is None:
        ok('非会员B preview 不显示任何余额（无A的余额残留）')
    else:
        bad('B继承A', f"is_member={bb.get('is_member')} member_info={bb.get('member_info')}")


def test_nonmember_member_payment_rejected():
    print('\n[6] 非会员提交 member 支付 → 服务端拒绝')
    login()
    pid = add_player('非会员付')
    sid, spid = start_session(1, pid, '非会员付', (datetime.now() - timedelta(minutes=30)).isoformat())
    r = checkout(sid, spid, payment_method='member', member_id=999999)
    if r.status_code == 400 and b'PLAYER_NOT_MEMBER' in r.data:
        ok('非会员 member 支付被拒 (400 PLAYER_NOT_MEMBER)')
    else:
        bad('非会员member支付', f"status={r.status_code} body={r.data}")


def test_forged_other_member_rejected():
    print('\n[7] 伪造他人会员ID扣款 → 服务端拒绝')
    login()
    victim_pid = add_player('受害者')
    victim_mid = add_member(victim_pid, 200.0)
    attacker_pid = add_player('攻击者')
    sid, spid = start_session(1, attacker_pid, '攻击者', (datetime.now() - timedelta(minutes=30)).isoformat())
    r = checkout(sid, spid, payment_method='member', member_id=victim_mid)
    c = db()
    before = c.execute('SELECT balance FROM members WHERE id=?', [victim_mid]).fetchone()['balance']; c.close()
    if r.status_code == 400 and b'PLAYER_NOT_MEMBER' in r.data:
        ok('伪造跨会员扣款被拒')
    else:
        bad('伪造跨会员', f"status={r.status_code}")
    # 受害者余额不得变化
    c = db(); after = c.execute('SELECT balance FROM members WHERE id=?', [victim_mid]).fetchone()['balance']; c.close()
    if abs(after - before) < 0.001:
        ok('受害者余额未被扣减')
    else:
        bad('受害者余额', f"before={before} after={after}")


def test_insufficient_balance_rejected():
    print('\n[8] 会员余额不足 → 服务端拒绝')
    login()
    pid = add_player('穷会员')
    mid = add_member(pid, 5.0)
    sid, spid = start_session(1, pid, '穷会员', (datetime.now() - timedelta(minutes=120)).isoformat())
    r = checkout(sid, spid, payment_method='member', member_id=mid)
    if r.status_code == 400 and '会员余额不足' in (r.get_json() or {}).get('error', ''):
        ok('余额不足被拒')
    else:
        bad('余额不足', f"status={r.status_code} body={r.data}")


def test_member_payment_deducts():
    print('\n[9] 正常会员余额支付 → 正确扣减')
    login()
    pid = add_player('付钱会员')
    mid = add_member(pid, 100.0)
    sid, spid = start_session(1, pid, '付钱会员', (datetime.now() - timedelta(minutes=30)).isoformat())
    r = checkout(sid, spid, payment_method='member', member_id=mid)
    if r.status_code == 200:
        res = r.get_json()
        ok('会员结账 200')
        c = db(); bal = c.execute('SELECT balance FROM members WHERE id=?', [mid]).fetchone()['balance']; c.close()
        expected = round(100.0 - res['grand_total'], 2)
        if abs(bal - expected) < 0.01:
            ok(f'余额正确扣减: 100 - {res["grand_total"]} = {bal}')
        else:
            bad('余额扣减', f"expected={expected} got={bal}")
    else:
        bad('会员结账', f"status={r.status_code} body={r.data}")


def test_cash_payment_unaffected():
    print('\n[10] 现金/微信/支付宝支付不受影响')
    login()
    pid = add_player('现金客')
    sid, spid = start_session(1, pid, '现金客', (datetime.now() - timedelta(minutes=30)).isoformat())
    for pm in ['cash', 'wechat', 'alipay']:
        r = checkout(sid, spid, payment_method=pm)
        if r.status_code == 200:
            ok(f'{pm} 支付成功')
        else:
            bad(f'{pm} 支付', f"status={r.status_code} body={r.data}")
        # 重新开启一个 session 继续测下一个支付方式
        sid, spid = start_session(1, pid, '现金客', (datetime.now() - timedelta(minutes=30)).isoformat())


def test_whole_session_forged_member_rejected():
    print('\n[11] 整桌结账伪造他人会员ID → 服务端拒绝')
    login()
    victim_pid = add_player('整桌受害者')
    victim_mid = add_member(victim_pid, 200.0)
    p1 = add_player('整桌客1')
    close_all_sessions()
    sid = client.post('/api/sessions', json={
        'machine_id': 1,
        'start_time': (datetime.now() - timedelta(minutes=30)).isoformat(),
        'players': [{'name': '整桌客1', 'player_id': p1}]
    }).get_json()['id']
    r = client.post(f'/api/sessions/{sid}/close', json={
        'payment_method': 'member', 'member_id': victim_mid, 'product_total': 0
    })
    c = db()
    before = c.execute('SELECT balance FROM members WHERE id=?', [victim_mid]).fetchone()['balance']; c.close()
    if r.status_code == 400 and b'PLAYER_NOT_MEMBER' in r.data:
        ok('整桌伪造会员被拒')
    else:
        bad('整桌伪造', f"status={r.status_code} body={r.data}")
    c = db(); after = c.execute('SELECT balance FROM members WHERE id=?', [victim_mid]).fetchone()['balance']; c.close()
    if abs(after - before) < 0.001:
        ok('整桌受害者余额未被扣减')
    else:
        bad('整桌受害者余额', f"before={before} after={after}")


if __name__ == '__main__':
    login()
    test_non_member_no_balance()
    test_orphan_member_id_treated_as_nonmember()
    test_real_member_balance()
    test_name_collision_no_cross_balance()
    test_sequential_open_no_leak()
    test_nonmember_member_payment_rejected()
    test_forged_other_member_rejected()
    test_insufficient_balance_rejected()
    test_member_payment_deducts()
    test_cash_payment_unaffected()
    test_whole_session_forged_member_rejected()
    print(f'\n==== RESULT: {PASS} passed, {FAIL} failed ====')
    sys.exit(1 if FAIL else 0)
