"""整桌结账与结算备注 - 验收测试

覆盖设计说明 6 个验收场景：
  1. 逐人应收=实收可不填备注
  2. 逐人应收≠实收空备注被拒，填备注成功
  3. 整桌约定台费+未结商品正确汇总、只产生一笔营业额（不双计）
  4. 整桌实收差无备注被拒、有备注后成功且流水可见
  5. 整桌后所有未结玩家与商品已结算、重复结算被拒
  6. 已有逐人结账与历史记录的显示/统计不变（不破坏原逻辑）

注意：测试用会话的开台时间可能在「今天凌晨前」，因此日报/CSV 查询一律按「会话实际日期」过滤，
避免跨零点导致会话落在上一天而查不到。
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


def start_session(machine_id, players, start_time=None):
    payload = {'machine_id': machine_id, 'players': players}
    if start_time:
        payload['start_time'] = start_time
    r = client.post('/api/sessions', json=payload)
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    spids = [row['id'] for row in db.execute('SELECT id FROM session_players WHERE session_id=? ORDER BY id', [sid])]
    db.close()
    return sid, spids


def session_date(sid):
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    st = db.execute('SELECT start_time FROM sessions WHERE id=?', [sid]).fetchone()['start_time']
    db.close()
    return st[:10]  # YYYY-MM-DD


def get_session_players(sid):
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute('SELECT * FROM session_players WHERE session_id=?', [sid])]
    db.close()
    return rows


def get_table_settlements(sid):
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    rows = [dict(r) for r in db.execute('SELECT * FROM table_settlements WHERE session_id=?', [sid])]
    db.close()
    return rows


def sell_product(sid, spid, product_id=1, quantity=1):
    r = client.post('/api/products/sell', json={
        'session_id': sid, 'session_player_id': spid, 'product_id': product_id, 'quantity': quantity
    })
    assert r.status_code == 201, r.data
    return r.get_json()['total']


def get_daily(date):
    r = client.get(f'/api/daily?date={date}')
    assert r.status_code == 200, r.data
    return r.get_json()


def daily_for_session(sid):
    return get_daily(session_date(sid))


# ===== 场景 1：逐人应收=实收可不填备注 =====
def test_per_person_no_diff_no_note():
    print('\n[1] 逐人应收=实收可不填备注')
    pid = add_player('逐人A_1')
    sid, [spid] = start_session(1, [{'name': '逐人A_1', 'player_id': pid}])

    # 实收不传 -> 默认等于应收；不应强制备注
    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0, 'note': ''
    })
    if r.status_code != 200:
        bad('逐人应收=实收成功(应200)', f'status={r.status_code}, body={r.data[:300]}')
        return
    res = r.get_json()
    assert abs((res.get('difference') or 0)) < 0.01, f'差额应为0，实际{res.get("difference")}'

    d = daily_for_session(sid)
    sess = next((x for x in d['sessions'] if x['id'] == sid), None)
    if not sess:
        bad('日报含该逐人桌', '未找到')
        return
    assert sess['settlement_type'] == 'player', f'settlement_type={sess["settlement_type"]}'
    ok('逐人应收=实收可不填备注')


# ===== 场景 2：逐人应收≠实收，空备注被拒；填备注成功 =====
def test_per_person_diff_requires_note():
    print('\n[2] 逐人应收≠实收 空备注被拒 / 填备注成功')
    pid = add_player('逐人B_2')
    sid, [spid] = start_session(2, [{'name': '逐人B_2', 'player_id': pid}])
    WRONG = 12345.67  # 明显不同于应收，用于触发差額校验

    # 2a. 空备注被拒
    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0,
        'actual_amount': WRONG, 'note': ''
    })
    if r.status_code != 400:
        bad('空备注被拒(应400)', f'status={r.status_code}')
        return
    assert '备注' in r.get_json().get('error', ''), r.get_json()

    # 2b. 填备注成功
    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0,
        'actual_amount': WRONG, 'note': '顾客抹零优惠'
    })
    if r.status_code != 200:
        bad('填备注成功(应200)', f'status={r.status_code}, body={r.data[:300]}')
        return
    res = r.get_json()
    assert abs((res.get('actual_amount') or 0) - (res.get('expected_amount') or 0)) > 0.01, '应收/实收应不同'
    assert res.get('note') == '顾客抹零优惠', res.get('note')

    d = daily_for_session(sid)
    sess = next((x for x in d['sessions'] if x['id'] == sid), None)
    if not sess:
        bad('日报含差额/备注', '未找到')
        return
    assert abs(sess['difference']) > 0.01, f'日报差额={sess["difference"]}'
    assert sess['note'] == '顾客抹零优惠', sess['note']
    ok('逐人应收≠实收 空备注被拒 / 填备注成功')


# ===== 场景 3：整桌约定台费+未结商品正确汇总，只产生一笔营业额（不双计）=====
def test_table_settlement_aggregation():
    print('\n[3] 整桌约定台费+未结商品汇总 / 只计一笔（不双计）')
    p1 = add_player('整桌C_1')
    p2 = add_player('整桌C_2')
    sid, [sp1, sp2] = start_session(3, [
        {'name': '整桌C_1', 'player_id': p1}, {'name': '整桌C_2', 'player_id': p2}
    ])

    # 两人均挂未结商品（不同金额，验证各自归属保留）
    prod1 = sell_product(sid, sp1, product_id=1, quantity=2)
    prod2 = sell_product(sid, sp2, product_id=2, quantity=1)
    product_total = round(prod1 + prod2, 2)

    agreed = 200.0
    r = client.post(f'/api/sessions/{sid}/table-checkout', json={
        'agreed_table_fee': agreed, 'actual_amount': agreed + product_total,
        'payment_method': 'cash', 'note': '', 'payer_name': '整桌C_1'
    })
    if r.status_code != 200:
        bad('整桌结账成功', f'status={r.status_code}, body={r.data[:400]}')
        return
    res = r.get_json()
    assert abs(res['expected_amount'] - round(agreed + product_total, 2)) < 0.01, f'应收={res["expected_amount"]}'
    assert abs(res['actual_amount'] - round(agreed + product_total, 2)) < 0.01, f'实收={res["actual_amount"]}'
    assert abs(res['difference']) < 0.01, f'差额={res["difference"]}'

    # 只产生一笔整桌结算记录
    ts = get_table_settlements(sid)
    if len(ts) != 1:
        bad('只产生一笔整桌结算', f'记录数={len(ts)}')
        return

    # 未结玩家全部 table_settled，grand_total=0（避免按人重复计入）
    players = get_session_players(sid)
    if any(p['status'] != 'table_settled' for p in players):
        bad('玩家全部 table_settled', str([p['status'] for p in players]))
        return
    if any(abs((p['grand_total'] or 0)) > 0.01 for p in players):
        bad('玩家 grand_total 置0', str([p['grand_total'] for p in players]))
        return

    # 商品仍保留各自玩家归属且已结算（不并入整桌台费）
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    ps = [dict(r) for r in db.execute('SELECT * FROM product_sales WHERE session_id=?', [sid])]
    db.close()
    if any(p['status'] != 'SETTLED' for p in ps):
        bad('商品全部已结算', str([p['status'] for p in ps]))
        return
    if any(p['session_player_id'] is None for p in ps):
        bad('商品保留玩家归属', '存在无归属商品')
        return

    # 日报：该桌只计一次，台费实收=实收-商品，避免与商品双计
    d = daily_for_session(sid)
    sess = next((x for x in d['sessions'] if x['id'] == sid), None)
    if not sess:
        bad('日报含整桌桌', '未找到')
        return
    assert sess['settlement_type'] == 'table', sess['settlement_type']
    fee_actual = round(res['actual_amount'] - product_total, 2)
    if abs(sess['final_fee'] - fee_actual) > 0.01:
        bad('台费实收不双计', f'final_fee={sess["final_fee"]} 期望{fee_actual}')
        return
    if abs(sess['product_total'] - product_total) > 0.01:
        bad('商品合计正确', f'product_total={sess["product_total"]} 期望{product_total}')
        return
    ok('整桌汇总正确 / 只计一笔 / 不双计')


# ===== 场景 4：整桌实收差无备注被拒；有备注后成功且流水可见 =====
def test_table_settlement_diff_requires_note():
    print('\n[4] 整桌实收差 空备注被拒 / 有备注成功且流水可见')
    p1 = add_player('整桌D_1')
    p2 = add_player('整桌D_2')
    sid, [sp1, sp2] = start_session(4, [
        {'name': '整桌D_1', 'player_id': p1}, {'name': '整桌D_2', 'player_id': p2}
    ])
    sell_product(sid, sp1, product_id=1, quantity=1)
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    product_total = db.execute('SELECT COALESCE(SUM(total),0) t FROM product_sales WHERE session_id=? AND status="UNSETTLED"', [sid]).fetchone()['t']
    db.close()
    agreed = 180.0
    expected = round(agreed + product_total, 2)

    # 4a. 空备注被拒
    r = client.post(f'/api/sessions/{sid}/table-checkout', json={
        'agreed_table_fee': agreed, 'actual_amount': round(expected - 20, 2),
        'payment_method': 'cash', 'note': '', 'payer_name': ''
    })
    if r.status_code != 400:
        bad('整桌空备注被拒(应400)', f'status={r.status_code}')
        return
    assert '备注' in r.get_json().get('error', ''), r.get_json()

    # 4b. 有备注成功
    r = client.post(f'/api/sessions/{sid}/table-checkout', json={
        'agreed_table_fee': agreed, 'actual_amount': round(expected - 20, 2),
        'payment_method': 'cash', 'note': '整桌议价优惠20', 'payer_name': '整桌D_1'
    })
    if r.status_code != 200:
        bad('整桌有备注成功(应200)', f'status={r.status_code}, body={r.data[:400]}')
        return
    res = r.get_json()
    assert abs((res.get('difference') or 0) + 20) < 0.01, f'差额应=-20，实际{res.get("difference")}'

    d = daily_for_session(sid)
    sess = next((x for x in d['sessions'] if x['id'] == sid), None)
    if not sess:
        bad('日报含整桌差额', '未找到')
        return
    assert sess['settlement_type'] == 'table', sess['settlement_type']
    assert abs(sess['difference'] + 20) < 0.01, f'日报差额={sess["difference"]}'
    assert sess['note'] == '整桌议价优惠20', sess['note']
    assert '整桌D_1' in (sess.get('payer_name') or ''), sess.get('payer_name')
    ok('整桌实收差 空备注被拒 / 有备注成功且流水可见')


# ===== 场景 5：整桌后所有未结玩家与商品已结算；重复结算被拒 =====
def test_table_settlement_repeat_rejected():
    print('\n[5] 整桌后玩家/商品已结算；重复整桌结账被拒')
    p1 = add_player('整桌E_1')
    p2 = add_player('整桌E_2')
    sid, [sp1, sp2] = start_session(1, [
        {'name': '整桌E_1', 'player_id': p1}, {'name': '整桌E_2', 'player_id': p2}
    ])
    sell_product(sid, sp1, product_id=1, quantity=1)
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    product_total = db.execute('SELECT COALESCE(SUM(total),0) t FROM product_sales WHERE session_id=? AND status="UNSETTLED"', [sid]).fetchone()['t']
    db.close()

    r = client.post(f'/api/sessions/{sid}/table-checkout', json={
        'agreed_table_fee': 150.0, 'actual_amount': round(150.0 + product_total, 2),
        'payment_method': 'cash', 'note': '', 'payer_name': ''
    })
    if r.status_code != 200:
        bad('首次整桌结账成功', f'status={r.status_code}')
        return

    # 所有未结玩家已结算
    players = get_session_players(sid)
    if any(p['status'] == 'playing' for p in players):
        bad('无未结玩家残留', str([p['status'] for p in players]))
        return
    # 所有商品已结算
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    unsettled = db.execute('SELECT COUNT(*) c FROM product_sales WHERE session_id=? AND status="UNSETTLED"', [sid]).fetchone()['c']
    db.close()
    if unsettled != 0:
        bad('无未结商品残留', f'未结商品={unsettled}')
        return

    # 重复整桌结账被拒（已无未结玩家 / 桌已关闭）
    r = client.post(f'/api/sessions/{sid}/table-checkout', json={
        'agreed_table_fee': 150.0, 'actual_amount': 150.0,
        'payment_method': 'cash', 'note': '', 'payer_name': ''
    })
    if r.status_code != 400:
        bad('重复整桌结账被拒(应400)', f'status={r.status_code}')
        return
    ok('整桌后玩家/商品已结算；重复结算被拒')


# ===== 场景 6：已有逐人结账与历史记录显示/统计/导出不变（不破坏原逻辑）=====
def test_existing_per_person_unchanged():
    print('\n[6] 逐人结账与历史记录显示/统计不变')
    pid = add_player('历史F_1')
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    sid, [spid] = start_session(2, [{'name': '历史F_1', 'player_id': pid}], start)
    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0
    })
    if r.status_code != 200:
        bad('标准逐人结账', f'status={r.status_code}')
        return
    res = r.get_json()
    # 不破坏原字段
    assert res['grand_total'] > 0, '原 grand_total 应保持'
    assert res.get('actual_amount') is None or abs(res.get('actual_amount') - res['grand_total']) < 0.01

    # 不应生成整桌结算记录
    ts = get_table_settlements(sid)
    if ts:
        bad('逐人结账不生成整桌记录', f'记录数={len(ts)}')
        return
    # 日报按逐人口径统计，且该桌 settlement_type=player
    d = daily_for_session(sid)
    sess = next((x for x in d['sessions'] if x['id'] == sid), None)
    if not sess:
        bad('日报含逐人桌', '未找到')
        return
    if sess['settlement_type'] != 'player':
        bad('逐人桌口径不变', f'settlement_type={sess["settlement_type"]}')
        return
    # 导出 CSV 不报错且含该玩家（按会话实际日期过滤）
    r = client.get(f'/api/export/csv?start_date={session_date(sid)}')
    if r.status_code != 200:
        bad('CSV 导出', f'status={r.status_code}')
        return
    assert '历史F_1' in r.get_data(as_text=True), 'CSV 应包含玩家'
    ok('逐人结账与历史记录显示/统计/导出不变')


if __name__ == '__main__':
    login()
    test_per_person_no_diff_no_note()
    test_per_person_diff_requires_note()
    test_table_settlement_aggregation()
    test_table_settlement_diff_requires_note()
    test_table_settlement_repeat_rejected()
    test_existing_per_person_unchanged()
    print(f'\n汇总: PASS={PASS}, FAIL={FAIL}')
    sys.exit(0 if FAIL == 0 else 1)
