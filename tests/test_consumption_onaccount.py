"""游戏中玩家消费挂账（on-account）端到端测试。

覆盖：
- 游戏中添加有码/无码商品并持久化到数据库
- 刷新/新请求后仍可读取（换店员不丢）
- 消费绑定正确的 session / session_player / player
- 单人结账自动带出未结算消费并计入总额
- 结账后商品状态变 SETTLED，且已结算不会重复计费
- 挂错的未结算商品可删除（含库存归还），已结算不可删
- 多商品金额累加
- 有码/无码原柜台逻辑不被破坏
- 台费/折扣/支付确认回归正常
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


def close_all_sessions():
    """关闭所有仍活跃的对局，释放台桌，避免测试间机器占用互相干扰。"""
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    active = db.execute("SELECT id FROM sessions WHERE status='active'").fetchall()
    db.close()
    for row in active:
        try:
            client.post(f'/api/sessions/{row["id"]}/force-close', json={})
        except Exception:
            pass


def start_session(machine_id, player_id, player_name, start_time):
    close_all_sessions()
    r = client.post('/api/sessions', json={
        'machine_id': machine_id,
        'start_time': start_time,
        'players': [{'name': player_name, 'player_id': player_id}]
    })
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    spid = db.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    db.close()
    return sid, spid


def db_query(sql, args=()):
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    row = db.execute(sql, args).fetchone()
    db.close()
    return dict(row) if row else None


def db_query_all(sql, args=()):
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(sql, args).fetchall()
    db.close()
    return [dict(r) for r in rows]


def sell_onaccount(sid, spid, **payload):
    payload.setdefault('session_id', sid)
    payload.setdefault('session_player_id', spid)
    return client.post('/api/products/sell', json=payload)


def get_consumption(sid, spid):
    return client.get(f'/api/sessions/{sid}/players/{spid}/consumption')


def test_add_code_product_onaccount():
    print('\n[1] 游戏中添加有码商品并持久化')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('汤包哥')
    sid, spid = start_session(1, pid, '汤包哥', start)

    r = sell_onaccount(sid, spid, product_id=1, quantity=2)
    if r.status_code != 201:
        bad('游戏中加有码商品', f'status={r.status_code}, body={r.data[:200]}'); return
    sale = db_query('SELECT * FROM product_sales WHERE id=?', [r.get_json()['sale_id']])
    if not sale:
        bad('游戏中加有码商品', '数据库无记录'); return
    if sale['session_player_id'] != spid:
        bad('游戏中加有码商品', f'session_player_id 不匹配 {sale["session_player_id"]} vs {spid}'); return
    if sale['player_id'] != pid:
        bad('游戏中加有码商品', f'player_id 不匹配 {sale["player_id"]} vs {pid}'); return
    if sale['status'] != 'UNSETTLED':
        bad('游戏中加有码商品', f'状态应为 UNSETTLED，实际 {sale["status"]}'); return
    ok('游戏中加有码商品并持久化')


def test_add_custom_product_onaccount():
    print('\n[2] 游戏中添加无码商品并持久化')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('无码客')
    sid, spid = start_session(2, pid, '无码客', start)

    r = sell_onaccount(sid, spid, is_custom=True, custom_name='槟榔', custom_category='other', price=10, quantity=1)
    if r.status_code != 201:
        bad('游戏中加无码商品', f'status={r.status_code}, body={r.data[:200]}'); return
    sale = db_query('SELECT * FROM product_sales WHERE id=?', [r.get_json()['sale_id']])
    if not sale or sale['status'] != 'UNSETTLED' or sale['is_custom'] != 1:
        bad('游戏中加无码商品', '未正确持久化为无码未结算'); return
    ok('游戏中加无码商品并持久化')


def test_persist_across_refresh():
    print('\n[3] 刷新/新请求后仍可读取（换店员不丢）')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('换店员客')
    sid, spid = start_session(1, pid, '换店员客', start)

    r = sell_onaccount(sid, spid, product_id=1, quantity=1)
    assert r.status_code == 201

    # 模拟刷新：重新拉取多次
    c1 = get_consumption(sid, spid).get_json()
    c2 = get_consumption(sid, spid).get_json()
    if len(c1.get('items', [])) != 1 or len(c2.get('items', [])) != 1:
        bad('刷新后仍可读', f'items 数量异常 c1={len(c1.get("items",[]))} c2={len(c2.get("items",[]))}'); return
    # 换一个“新请求上下文”也仍可读（DB 持久化）
    again = client.get(f'/api/sessions/{sid}/players/{spid}/consumption').get_json()
    if len(again.get('items', [])) != 1:
        bad('刷新后仍可读', '新请求未读到挂账'); return
    ok('刷新/新请求后仍可读取')


def test_checkout_auto_reads_unsettled():
    print('\n[4] 单人结账自动带出未结算消费并计入总额')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('带出客')
    sid, spid = start_session(1, pid, '带出客', start)

    r = sell_onaccount(sid, spid, product_id=1, quantity=2)
    assert r.status_code == 201
    sold_total = r.get_json()['total']

    preview = client.get(f'/api/sessions/{sid}/players/{spid}/preview').get_json()
    if len(preview.get('product_sales', [])) != 1:
        bad('结账自动带出', f'preview 未带出挂账，items={len(preview.get("product_sales",[]))}'); return
    if abs(preview['product_total'] - sold_total) > 0.001:
        bad('结账自动带出', f'product_total 不符 {preview["product_total"]} vs {sold_total}'); return

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None,
        'product_total': sold_total, 'discount_id': None,
        'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0
    })
    if r.status_code != 200:
        bad('结账自动带出', f'结账失败 status={r.status_code} body={r.data[:200]}'); return
    res = r.get_json()
    if abs(res['product_total'] - sold_total) > 0.001:
        bad('结账自动带出', '返回 product_total 不符'); return
    if abs(res['grand_total'] - round(res['final_fee'] + sold_total, 2)) > 0.001:
        bad('结账自动带出', 'grand_total 未含商品'); return
    ok('单人结账自动带出未结算消费并计入总额')


def test_settled_after_checkout_no_double_charge():
    print('\n[5] 结账后状态变 SETTLED 且不再重复计费')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('结算客')
    sid, spid = start_session(1, pid, '结算客', start)

    r = sell_onaccount(sid, spid, product_id=1, quantity=1)
    assert r.status_code == 201
    sold_total = r.get_json()['total']

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None,
        'product_total': sold_total, 'discount_id': None,
        'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0
    })
    assert r.status_code == 200, r.data

    # 已结算：状态变 SETTLED
    sale = db_query('SELECT * FROM product_sales WHERE session_player_id=?', [spid])
    if sale['status'] != 'SETTLED':
        bad('结算后不重复计费', f'状态应为 SETTLED，实际 {sale["status"]}'); return
    # 再次打开 preview（模拟换店员结账）不应再带入该商品
    preview = client.get(f'/api/sessions/{sid}/players/{spid}/preview').get_json()
    if len(preview.get('product_sales', [])) != 0:
        bad('结算后不重复计费', '已结算商品仍出现在 preview'); return
    # 再次结账应被拒（已结账）
    r2 = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None,
        'product_total': 0, 'discount_id': None,
        'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0
    })
    if r2.status_code != 400:
        bad('结算后不重复计费', f'重复结账应被拒，实际 {r2.status_code}'); return
    ok('结账后状态变 SETTLED 且不再重复计费')


def test_delete_unsettled_and_stock_restore():
    print('\n[6] 挂错的未结算商品可删除（有码归还库存）')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('删除客')
    mid = add_member(pid, 500)
    sid, spid = start_session(1, pid, '删除客', start)

    # 建一个可追踪库存的商品
    r = client.post('/api/products', json={'name': '测试可乐', 'category': 'drink', 'price': 5, 'stock': 10})
    assert r.status_code == 201, r.data
    prod_id = db_query('SELECT id FROM products WHERE name=?', ['测试可乐'])['id']

    r = sell_onaccount(sid, spid, product_id=prod_id, quantity=3)
    assert r.status_code == 201
    sale_id = r.get_json()['sale_id']
    stock_after_sell = db_query('SELECT stock FROM products WHERE id=?', [prod_id])['stock']
    if stock_after_sell != 7:
        bad('删除未结算', f'售卖后库存应为7，实际 {stock_after_sell}'); return

    # 删除该挂账
    r = client.delete(f'/api/product-sales/{sale_id}')
    if r.status_code != 200:
        bad('删除未结算', f'删除失败 status={r.status_code} body={r.data[:200]}'); return
    # 列表应清空
    c = get_consumption(sid, spid).get_json()
    if len(c.get('items', [])) != 0:
        bad('删除未结算', '删除后列表仍非空'); return
    # 库存应归还
    stock_after_del = db_query('SELECT stock FROM products WHERE id=?', [prod_id])['stock']
    if stock_after_del != 10:
        bad('删除未结算', f'删除后库存应归还为10，实际 {stock_after_del}'); return
    ok('挂错未结算商品可删除并归还库存')


def test_cannot_delete_settled():
    print('\n[7] 已结算消费不能普通删除')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('已结不可删')
    sid, spid = start_session(1, pid, '已结不可删', start)

    r = sell_onaccount(sid, spid, product_id=1, quantity=1)
    assert r.status_code == 201
    sale_id = r.get_json()['sale_id']
    sold_total = r.get_json()['total']

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None,
        'product_total': sold_total, 'discount_id': None,
        'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0
    })
    assert r.status_code == 200, r.data

    r = client.delete(f'/api/product-sales/{sale_id}')
    if r.status_code != 400:
        bad('已结算不可删', f'应返回400，实际 {r.status_code}'); return
    if '已结算' not in r.get_json().get('error', ''):
        bad('已结算不可删', '错误信息未提示已结算'); return
    ok('已结算消费不能普通删除')


def test_multiple_products_accumulate():
    print('\n[8] 多商品金额累加正确')
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    pid = add_player('累加客')
    sid, spid = start_session(1, pid, '累加客', start)

    r1 = sell_onaccount(sid, spid, product_id=1, quantity=2)  # 有码，单价见返回
    assert r1.status_code == 201
    t1 = r1.get_json()['total']
    r2 = sell_onaccount(sid, spid, is_custom=True, custom_name='槟榔', custom_category='other', price=10, quantity=3)
    assert r2.status_code == 201
    t2 = r2.get_json()['total']

    c = get_consumption(sid, spid).get_json()
    if len(c.get('items', [])) != 2:
        bad('多商品累加', f'应2条，实际 {len(c.get("items",[]))}'); return
    if abs(c['total'] - round(t1 + t2, 2)) > 0.001:
        bad('多商品累加', f'合计不符 {c["total"]} vs {round(t1+t2,2)}'); return
    ok('多商品金额累加正确')


def test_code_counter_sale_still_settled():
    print('\n[9] 有码原柜台逻辑：无场次玩家即时结算')
    # 不做游戏，直接卖（无 session_player_id），应判定为 SETTLED
    r = sell_onaccount(None, None, product_id=1, quantity=1)
    if r.status_code != 201:
        bad('有码柜台逻辑', f'status={r.status_code}'); return
    sale_id = r.get_json()['sale_id']
    sale = db_query('SELECT * FROM product_sales WHERE id=?', [sale_id])
    if sale['status'] != 'SETTLED':
        bad('有码柜台逻辑', f'无场次应 SETTLED，实际 {sale["status"]}'); return
    if sale['session_player_id'] is not None:
        bad('有码柜台逻辑', '柜台售卖不应绑定 session_player'); return
    # 不应出现在任何玩家挂账列表（用一个不存在的 sp 查询）
    c = client.get('/api/sessions/99999/players/99999/consumption').get_json()
    if c.get('items'):
        bad('有码柜台逻辑', '柜台售卖不应进入玩家挂账'); return
    ok('有码原柜台逻辑不被破坏')


def test_custom_counter_sale_still_settled():
    print('\n[10] 无码原柜台逻辑：无场次玩家即时结算')
    r = sell_onaccount(None, None, is_custom=True, custom_name='矿泉水', custom_category='drink', price=3, quantity=2)
    if r.status_code != 201:
        bad('无码柜台逻辑', f'status={r.status_code}'); return
    sale = db_query('SELECT * FROM product_sales WHERE id=?', [r.get_json()['sale_id']])
    if sale['status'] != 'SETTLED':
        bad('无码柜台逻辑', f'应 SETTLED，实际 {sale["status"]}'); return
    ok('无码原柜台逻辑不被破坏')


def test_normal_checkout_regression():
    print('\n[11] 台费/折扣/结账回归正常')
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('回归客')
    sid, spid = start_session(1, pid, '回归客', start)
    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None,
        'product_total': 0, 'discount_id': None,
        'start_time': None, 'is_overnight': False,
        'manual_discount_type': 'amount', 'manual_discount_value': 5
    })
    if r.status_code != 200:
        bad('回归', f'status={r.status_code} body={r.data[:200]}'); return
    res = r.get_json()
    if abs(res['manual_discount_amount'] - 5) > 0.001:
        bad('回归', '手动折扣金额不符'); return
    if abs(res['final_fee'] - round(res['fee'] - 5, 2)) > 0.001:
        bad('回归', '最终台费不符'); return
    ok('台费/折扣/结账回归正常')


if __name__ == '__main__':
    login()
    test_add_code_product_onaccount()
    test_add_custom_product_onaccount()
    test_persist_across_refresh()
    test_checkout_auto_reads_unsettled()
    test_settled_after_checkout_no_double_charge()
    test_delete_unsettled_and_stock_restore()
    test_cannot_delete_settled()
    test_multiple_products_accumulate()
    test_code_counter_sale_still_settled()
    test_custom_counter_sale_still_settled()
    test_normal_checkout_regression()
    print(f'\n汇总: PASS={PASS}, FAIL={FAIL}')
    sys.exit(0 if FAIL == 0 else 1)
