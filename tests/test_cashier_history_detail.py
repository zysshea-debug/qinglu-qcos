"""收银流水详情 - 核对信息展示验收测试

覆盖设计说明 6 个验收场景（接口层）：
  1. 逐人结账有差额和备注时，详情中该玩家能同时看到应收/实收/差额/备注。
  2. 逐人结账无差额时，详情差额为 0、备注为空（前端据此显示 ¥0.00 与“无”）。
  3. 整桌结账详情顶部返回完整结算事实（约定台费/商品/应收/实收/差额/备注/付款人/支付方式）。
  4. 整桌玩家行标记为“由整桌结算”且不返回假实收（grand_total=0，由前端隐藏实收列）。
  5. 商品明细仍与原记录一致。
  6. 历史记录（actual_amount 缺失）仍可打开且标记 historical_compat，不伪造金额/备注。

测试库隔离：与 test_table_settlement.py 一致，临时 DB + 自行播种台桌，
兼容脚本直跑与 pytest conftest 逐测试重建空库两种模式。
"""
import os
import sys
import tempfile
import sqlite3
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# 测试专用环境变量（必须在 import config 之前设置，非生产凭据）
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.DB_PATH = _tmp.name  # pytest conftest 可能已提前绑定，需同步
models.init_db()


def _seed_test_machines():
    """V1.6 起 init_db 不再自动插入台桌（生产安全），测试库自行播种。"""
    db = sqlite3.connect(config.DB_PATH)
    for m in config.MACHINES:
        db.execute(
            'INSERT OR IGNORE INTO machines (id, name, type, status, sort_order) VALUES (?, ?, ?, "idle", ?)',
            [m['id'], m['name'], m['type'], m['sort_order']]
        )
    db.commit()
    db.close()


_seed_test_machines()
import app as APP

try:
    import pytest

    @pytest.fixture(autouse=True)
    def _seed_machines_per_test(_isolate_db_per_test):
        _seed_test_machines()
except ImportError:
    pass

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
    r = client.post('/api/auth/login', json={
        'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
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
    spids = [row['id'] for row in db.execute(
        'SELECT id FROM session_players WHERE session_id=? ORDER BY id', [sid])]
    db.close()
    return sid, spids


def detail(session_id):
    r = client.get(f'/api/cashier_history/detail?session_id={session_id}')
    assert r.status_code == 200, r.data[:300]
    body = r.get_json()
    assert body['status'] == 'ok', body
    return body


def two_hours_ago():
    return (datetime.now() - timedelta(hours=2)).isoformat()


def sell_product(sid, spid, product_id=1, quantity=1):
    r = client.post('/api/products/sell', json={
        'session_id': sid, 'session_player_id': spid,
        'product_id': product_id, 'quantity': quantity})
    assert r.status_code == 201, r.data
    return r.get_json()['total']


# ===== 场景 1：逐人结账有差额和备注 =====
def test_player_diff_and_note():
    print('\n[1] 逐人结账有差额和备注 -> 详情可核对')
    pid = add_player('详情逐人差额')
    start = two_hours_ago()
    sid, [spid] = start_session(1, [{'name': '详情逐人差额', 'player_id': pid}], start)

    # 应收（grand_total）在结账时才写入，故用固定实收；断言时从详情读回应收核对。
    actual = 12.34

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': start, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0,
        'actual_amount': actual, 'note': '顾客抹零'
    })
    if r.status_code != 200:
        bad('逐人差额结账(应200)', f'status={r.status_code}, body={r.data[:300]}')
        return

    res = detail(sid)
    if res.get('settlement_mode') != 'player':
        bad('结算模式=逐人', f'settlement_mode={res.get("settlement_mode")}')
        return
    row = next((p for p in res['players'] if p['id'] == spid), None)
    if not row:
        bad('详情含该玩家', '未找到')
        return
    expected = float(row.get('grand_total') or 0)
    if expected <= 0 or abs(actual - expected) < 0.01:
        bad('应收/实收应有差异', f'expected={expected}, actual={actual}')
        return
    for field, want in (
        ('expected_amount', expected),
        ('actual_amount', actual),
        ('difference', round(actual - expected, 2)),
        ('note', '顾客抹零'),
        ('display_actual_amount', actual),
        ('display_difference', round(actual - expected, 2)),
        ('display_note', '顾客抹零'),
    ):
        got = row.get(field)
        if isinstance(want, float):
            if got is None or abs(float(got) - want) > 0.01:
                bad(f'字段 {field}', f'got={got}, want={want}')
                return
        elif got != want:
            bad(f'字段 {field}', f'got={got!r}, want={want!r}')
            return
    if row.get('historical_compat') is not False:
        bad('非历史记录', f'historical_compat={row.get("historical_compat")}')
        return
    if row.get('grand_total') != expected:
        bad('应收=grand_total', f'grand_total={row.get("grand_total")}, expected={expected}')
        return
    ok('逐人差额/备注在详情中可核对')


# ===== 场景 2：逐人结账无差额 =====
def test_player_no_diff():
    print('\n[2] 逐人结账无差额 -> 差额 0 / 备注空（前端显示 ¥0.00 与“无”）')
    pid = add_player('详情逐人无差额')
    start = two_hours_ago()
    sid, [spid] = start_session(2, [{'name': '详情逐人无差额', 'player_id': pid}], start)

    r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': start, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0, 'note': ''
    })
    if r.status_code != 200:
        bad('逐人无差额结账(应200)', f'status={r.status_code}, body={r.data[:300]}')
        return

    res = detail(sid)
    row = next((p for p in res['players'] if p['id'] == spid), None)
    if not row:
        bad('详情含该玩家', '未找到')
        return
    if abs(float(row.get('display_difference') or 0)) > 0.001:
        bad('差额为0', f'display_difference={row.get("display_difference")}')
        return
    if (row.get('display_note') or '') != '':
        bad('备注为空', f'display_note={row.get("display_note")!r}')
        return
    if abs(float(row.get('expected_amount') or 0) - float(row.get('grand_total') or 0)) > 0.001:
        bad('应收=grand_total', f'expected={row.get("expected_amount")}, gt={row.get("grand_total")}')
        return
    if abs(float(row.get('display_actual_amount') or 0) - float(row.get('expected_amount') or 0)) > 0.001:
        bad('实收=应收', f'actual={row.get("display_actual_amount")}')
        return
    ok('逐人无差额显示 0 / 无备注')


# ===== 场景 3+4+5：整桌结算事实、玩家行、商品明细 =====
def test_table_settlement_facts():
    print('\n[3] 整桌结账 -> 顶部结算事实完整 / 玩家不显示假实收 / 商品明细不变')
    p1 = add_player('详情整桌甲')
    p2 = add_player('详情整桌乙')
    start = two_hours_ago()
    sid, [sp1, sp2] = start_session(3, [
        {'name': '详情整桌甲', 'player_id': p1},
        {'name': '详情整桌乙', 'player_id': p2},
    ], start)

    prod1 = sell_product(sid, sp1, product_id=1, quantity=2)
    prod2 = sell_product(sid, sp2, product_id=2, quantity=1)
    product_total = round(prod1 + prod2, 2)
    agreed = 200.0
    expected = round(agreed + product_total, 2)
    actual = round(expected - 4.0, 2)  # 差额需备注

    r = client.post(f'/api/sessions/{sid}/table-checkout', json={
        'agreed_table_fee': agreed, 'actual_amount': actual,
        'payment_method': 'cash', 'note': '饮料赠送', 'payer_name': '详情整桌甲'
    })
    if r.status_code != 200:
        bad('整桌结账(应200)', f'status={r.status_code}, body={r.data[:400]}')
        return

    res = detail(sid)
    if res.get('settlement_mode') != 'table':
        bad('结算模式=整桌', f'settlement_mode={res.get("settlement_mode")}')
        return
    ts = res.get('table_settlement')
    if not ts:
        bad('返回整桌结算记录', 'table_settlement 缺失')
        return
    for field, want in (
        ('agreed_table_fee', agreed),
        ('product_total', product_total),
        ('expected_amount', expected),
        ('actual_amount', actual),
        ('difference', round(actual - expected, 2)),
        ('note', '饮料赠送'),
        ('payer_name', '详情整桌甲'),
        ('payment_method', 'cash'),
    ):
        got = ts.get(field)
        if isinstance(want, float):
            if got is None or abs(float(got) - want) > 0.01:
                bad(f'table_settlement.{field}', f'got={got}, want={want}')
                return
        elif got != want:
            bad(f'table_settlement.{field}', f'got={got!r}, want={want!r}')
            return

    # 玩家行：全部由整桌结算，grand_total=0（前端据此不展示假实收）
    if not res['players'] or any(p.get('status') != 'table_settled' for p in res['players']):
        bad('玩家均由整桌结算', f'{[p.get("status") for p in res["players"]]}')
        return
    if any(abs(float(p.get('grand_total') or 0)) > 0.001 for p in res['players']):
        bad('玩家 grand_total 为0', f'{[p.get("grand_total") for p in res["players"]]}')
        return
    # 商品明细仍为原两条记录，归属保留
    if len(res['products']) != 2:
        bad('商品明细条数', f'got={len(res["products"])}, want=2')
        return
    attributed = {p.get('session_player_id') for p in res['products']}
    if attributed != {sp1, sp2}:
        bad('商品玩家归属', f'got={attributed}, want={{{sp1}, {sp2}}}')
        return
    ok('整桌结算事实 / 玩家标记 / 商品明细均正确')


# ===== 场景 6：历史记录可打开且不伪造缺失数据 =====
def test_historical_compat():
    print('\n[6] 历史记录 -> 可打开 / 标记 historical_compat / 不伪造实收与备注')
    pid = add_player('详情历史玩家')
    start = two_hours_ago()
    sid, [spid] = start_session(4, [{'name': '详情历史玩家', 'player_id': pid}], start)

    # 模拟旧式记录：桌局已关闭、玩家已结账，但缺新增的实收/差额/备注字段
    db = sqlite3.connect(config.DB_PATH)
    db.execute(
        "UPDATE sessions SET status='closed', end_time=?, business_date='2026-01-01' WHERE id=?",
        [(datetime.now()).isoformat(), sid])
    db.execute(
        "UPDATE session_players SET status='checked_out', grand_total=123.45, "
        "actual_amount=NULL, difference=NULL, note=NULL WHERE id=?", [spid])
    db.commit()
    db.close()

    res = detail(sid)  # 不应因缺字段报错
    if res.get('settlement_mode') != 'player':
        bad('历史记录结算模式=逐人', f'settlement_mode={res.get("settlement_mode")}')
        return
    row = next((p for p in res['players'] if p['id'] == spid), None)
    if not row:
        bad('详情含历史玩家', '未找到')
        return
    if row.get('historical_compat') is not True:
        bad('标记历史兼容', f'historical_compat={row.get("historical_compat")}')
        return
    if row.get('actual_amount') is not None:
        bad('不伪造实收字段', f'actual_amount={row.get("actual_amount")!r}')
        return
    if abs(float(row.get('display_actual_amount') or 0) - 123.45) > 0.01:
        bad('兼容显示应收', f'display_actual_amount={row.get("display_actual_amount")}')
        return
    if abs(float(row.get('display_difference') or 0)) > 0.001:
        bad('兼容差额为0', f'display_difference={row.get("display_difference")}')
        return
    if (row.get('display_note') or '') != '':
        bad('不伪造备注', f'display_note={row.get("display_note")!r}')
        return
    ok('历史记录可打开 / 不伪造缺失数据')


if __name__ == '__main__':
    login()
    test_player_diff_and_note()
    test_player_no_diff()
    test_table_settlement_facts()
    test_historical_compat()
    print(f'\n汇总: PASS={PASS}, FAIL={FAIL}')
    sys.exit(0 if FAIL == 0 else 1)
