"""单人结账端到端测试（会员/扫码/商品/折扣）"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

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


def start_session(machine_id, player_id, player_name, start_time):
    r = client.post('/api/sessions', json={
        'machine_id': machine_id,
        'start_time': start_time,
        'players': [{'name': player_name, 'player_id': player_id}]
    })
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    import sqlite3
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    spid = db.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    db.close()
    return sid, spid


def test_member_checkout():
    print('\n[1] 会员余额结账')
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('天青')
    mid = add_member(pid, 100)
    sid, spid = start_session(1, pid, '天青', start)

    r = client.get(f'/api/sessions/{sid}/players/{spid}/preview')
    assert r.status_code == 200, r.data
    preview = r.get_json()
    assert preview['member_info'] is not None, '应识别会员'
    assert preview['fee'] > 0, f'台费应大于0，实际{preview["fee"]}'

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'member',
        'member_id': mid,
        'product_total': 0,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0
    })
    if r.status_code != 200:
        bad('会员结账', f'status={r.status_code}, body={r.data[:300]}')
        return
    result = r.get_json()
    assert result['payment_method'] == 'member'
    assert result['grand_total'] > 0, '应收应大于0'
    assert result['member_balance_after'] == 100 - result['grand_total']
    ok('会员结账')


def test_scan_checkout():
    print('\n[2] 微信扫码结账')
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(hours=1, minutes=30)).isoformat()
    pid = add_player('扫码测试')
    sid, spid = start_session(2, pid, '扫码测试', start)

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'scan_wechat',
        'member_id': None,
        'product_total': 0,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0
    })
    if r.status_code != 200:
        bad('扫码结账', f'status={r.status_code}, body={r.data[:300]}')
        return
    result = r.get_json()
    assert result['payment_method'] == 'scan_wechat'
    assert result['member_balance_after'] is None
    ok('扫码结账')


def test_checkout_with_product():
    print('\n[3] 结账含商品')
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('商品测试')
    mid = add_member(pid, 200)
    sid, spid = start_session(3, pid, '商品测试', start)

    # 先销售商品
    r = client.post('/api/products/sell', json={
        'session_id': sid,
        'session_player_id': spid,
        'product_id': 1,
        'quantity': 2
    })
    assert r.status_code == 201, r.data
    product_total = r.get_json()['total']

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'member',
        'member_id': mid,
        'product_total': product_total,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0
    })
    if r.status_code != 200:
        bad('商品结账', f'status={r.status_code}, body={r.data[:300]}')
        return
    result = r.get_json()
    assert result['product_total'] == product_total
    assert result['grand_total'] == round(result['final_fee'] + product_total, 2)
    ok('商品结账')


def test_manual_discount():
    print('\n[4] 手动台费折扣')
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('折扣测试')
    sid, spid = start_session(1, pid, '折扣测试', start)

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash',
        'member_id': None,
        'product_total': 0,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': 'amount',
        'manual_discount_value': 10
    })
    if r.status_code != 200:
        bad('手动折扣结账', f'status={r.status_code}, body={r.data[:300]}')
        return
    result = r.get_json()
    assert result['manual_discount_amount'] == 10
    assert result['final_fee'] == round(result['fee'] - 10, 2)
    ok('手动折扣结账')


def test_lottery_discount():
    print('\n[5] 抽奖优惠券抵扣')
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('抽奖测试')
    sid, spid = start_session(1, pid, '抽奖测试', start)

    # 创建一张半价券
    r = client.post('/api/discounts', json={
        'lottery_date': datetime.now().isoformat()[:10],
        'player_name': '抽奖测试',
        'discount_type': 'half'
    })
    assert r.status_code == 201, r.data

    # 查询优惠券ID
    import sqlite3
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    discount_id = db.execute('SELECT id FROM discounts WHERE player_name=?', ['抽奖测试']).fetchone()['id']
    db.close()

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash',
        'member_id': None,
        'product_total': 0,
        'discount_id': discount_id,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0
    })
    if r.status_code != 200:
        bad('抽奖抵扣结账', f'status={r.status_code}, body={r.data[:300]}')
        return
    result = r.get_json()
    assert result['discount_amount'] > 0, '抵扣金额应大于0'
    ok('抽奖抵扣结账')


def test_insufficient_balance():
    print('\n[6] 会员余额不足拦截')
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(hours=5)).isoformat()
    pid = add_player('余额不足')
    mid = add_member(pid, 10)
    sid, spid = start_session(1, pid, '余额不足', start)

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'member',
        'member_id': mid,
        'product_total': 0,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0
    })
    if r.status_code != 400:
        bad('余额不足拦截', f'应返回400，实际{r.status_code}')
        return
    assert '余额不足' in r.get_json().get('error', '')
    ok('余额不足拦截')


if __name__ == '__main__':
    login()
    test_member_checkout()
    test_scan_checkout()
    test_checkout_with_product()
    test_manual_discount()
    test_lottery_discount()
    test_insufficient_balance()
    print(f'\n汇总: PASS={PASS}, FAIL={FAIL}')
    sys.exit(0 if FAIL == 0 else 1)
