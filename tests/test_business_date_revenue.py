"""QCOS V1.6 营业日口径（business_date）核心测试

覆盖（任务三十四）：
    1. 跨午夜收入归属：8/22 23:00 开台、8/23 07:00 付款 → GMV 归 8/23（8/22 为 0）
    2. active 未结账桌 → 不进真实 GMV
    3. active 桌 → 进入「进行中预计」
    4. split_session_duration_by_date：跨天 / 跨月 / 跨年 / 同日 / 非法区间
    5. 多人分别结账：每人按各自支付时间归属营业日（A 归 8/22、B 归 8/23）
    6. 整台结账：按结账时间归属，不按开台日
    7. 穿透一致：revenue.real_gmv == 收银流水合计 == 分析中心 today_gmv

安全：全部使用临时库 + fake clock（revenue/overnight 模块内 datetime 替换），
不触碰真实 qcos.db。
"""

import os
import sys
import tempfile
from datetime import datetime, date

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name
config.PAYMENT['provider'] = None  # 关闭扫码支付通道门禁（测试不真实收款）

import models
models.init_db()
import app as APP
import revenue
import overnight
import analytics as A
from models import get_db

# ===== fake clock：revenue / overnight 内部 datetime 替换 =====
CURRENT = {'now': datetime(2026, 8, 23, 7, 0, 0)}


class _FC(datetime):
    @classmethod
    def now(cls, tz=None):
        return CURRENT['now']


# 先保存原始引用，模块测试结束后恢复（防止跨模块污染）：
# 若不复原，同进程内后续测试模块（如 test_checkout / test_cashier_history）
# 会继续读到被替换的 revenue/overnight datetime 与 analytics 时钟，
# 导致 effective_end_time / business_date 归属错乱（曾致 fee=0、business_date 差一天）。
_orig_revenue_dt = revenue.datetime
_orig_overnight_dt = overnight.datetime
_orig_analytics_now = A._now
_orig_analytics_today = A._today

revenue.datetime = _FC
overnight.datetime = _FC
# app 内是 `from datetime import datetime`，其 datetime.now() 不影响金额归属
# （归属只用 revenue.business_date_for / revenue.shanghai_now / overnight.effective_end_time）


@pytest.fixture(autouse=True, scope='module')
def _restore_clock_after_module():
    """模块测试结束后恢复真实时钟引用，避免污染同进程内后续测试模块。"""
    yield
    revenue.datetime = _orig_revenue_dt
    overnight.datetime = _orig_overnight_dt
    A._now = _orig_analytics_now
    A._today = _orig_analytics_today

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


def check(name, cond, msg=''):
    if cond:
        ok(name)
    else:
        bad(name, msg)


def login():
    r = client.post('/api/auth/login', json={
        'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    assert r.status_code == 200, r.data


def start_session(machine_id, start, players):
    r = client.post('/api/sessions', json={
        'machine_id': machine_id, 'start_time': start, 'players': players})
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def checkout_payload(payment='cash', **kw):
    base = {
        'payment_method': payment, 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0,
    }
    base.update(kw)
    return base


def checkout_player(sid, sp_id, **kw):
    r = client.post(f'/api/sessions/{sid}/players/{sp_id}/checkout', json=checkout_payload(**kw))
    assert r.status_code == 200, r.data
    return r.get_json()


# ===================== 1. 跨午夜桌时拆分（纯函数） =====================

def test_split():
    print('\n[1] split_session_duration_by_date')
    r = revenue.split_session_duration_by_date('2026-08-22T23:00:00', '2026-08-23T07:00:00')
    check('跨天: {8/22:60, 8/23:420}',
          r == {'2026-08-22': 60.0, '2026-08-23': 420.0}, str(r))
    r = revenue.split_session_duration_by_date('2026-07-31T23:00:00', '2026-08-01T01:00:00')
    check('跨月: {7/31:60, 8/1:60}',
          r == {'2026-07-31': 60.0, '2026-08-01': 60.0}, str(r))
    r = revenue.split_session_duration_by_date('2025-12-31T23:00:00', '2026-01-01T01:00:00')
    check('跨年: {12/31:60, 1/1:60}',
          r == {'2025-12-31': 60.0, '2026-01-01': 60.0}, str(r))
    r = revenue.split_session_duration_by_date('2026-08-23T10:00:00', '2026-08-23T12:00:00')
    check('同日: {8/23:120}', r == {'2026-08-23': 120.0}, str(r))
    r = revenue.split_session_duration_by_date('2026-08-23T12:00:00', '2026-08-23T10:00:00')
    check('end<=start 返回空', r == {}, str(r))
    r = revenue.split_session_duration_by_date('2026-08-23T00:00:00', '2026-08-23T00:00:00')
    check('零时长返回空', r == {}, str(r))


# ===================== 2. 跨午夜收入归属 =====================

def test_cross_midnight_attribution():
    print('\n[2] 跨午夜收入归属（8/22 23:00 开台 → 8/23 07:00 付款）')
    CURRENT['now'] = datetime(2026, 8, 23, 7, 0, 0)
    start = '2026-08-22T23:00:00'
    sid = start_session(1, start, [{'name': '跨夜测试', 'start_time': start}])
    sp_id = client.get(f'/api/sessions/{sid}/preview').get_json()['players'][0]['id']
    prev = client.get(f'/api/sessions/{sid}/players/{sp_id}/preview').get_json()
    amt = round(prev.get('fee') or 0, 2)
    res = checkout_player(sid, sp_id)
    db = get_db()
    check('8/23 真实GMV = 台费', abs(revenue.real_gmv(db, '2026-08-23') - amt) < 0.01,
          f"gmv={revenue.real_gmv(db, '2026-08-23')} amt={amt}")
    check('8/22 真实GMV = 0', revenue.real_gmv(db, '2026-08-22') == 0,
          str(revenue.real_gmv(db, '2026-08-22')))
    row = db.execute(
        'SELECT business_date, paid_at, operator FROM session_players WHERE id=?', [sp_id]).fetchone()
    check('sp.business_date = 8/23', row['business_date'] == '2026-08-23', str(dict(row)))
    check('sp.operator 已落库（显示名）', row['operator'] == '管理员', str(dict(row)))
    check('结账返回 session_closed', res.get('session_closed') is True)


# ===================== 3. active 不进 GMV / 进进行中预计 =====================

def test_active_excluded_from_gmv():
    print('\n[3] active 未结账桌：不进真实 GMV，只进进行中预计')
    CURRENT['now'] = datetime(2026, 8, 23, 7, 0, 0)
    db = get_db()
    before = revenue.real_gmv(db, '2026-08-23')
    start = '2026-08-23T05:00:00'
    start_session(2, start, [{'name': '进行中', 'start_time': start}])
    after = revenue.real_gmv(db, '2026-08-23')
    check('active 桌不入真实 GMV', abs(after - before) < 0.01, f'{before} -> {after}')
    check('active 桌数 = 1', revenue.active_session_count(db) == 1)
    est = revenue.in_progress_estimate(db)
    check('进行中预计 > 0', est > 0, f'est={est}')


# ===================== 4. 整台结账按支付时间归属 =====================

def test_whole_table_close():
    print('\n[4] 整台结账归属（8/22 22:00 开台 → 8/23 06:00 结账）')
    CURRENT['now'] = datetime(2026, 8, 23, 6, 0, 0)
    db = get_db()
    before = revenue.real_gmv(db, '2026-08-23')
    start = '2026-08-22T22:00:00'
    sid = start_session(3, start, [{'name': '整台结', 'start_time': start}])
    r = client.post(f'/api/sessions/{sid}/close', json={
        'payment_method': 'cash', 'product_total': 0, 'discount_id': None})
    assert r.status_code == 200, r.data
    amt = round(r.get_json()['grand_total'], 2)
    after = revenue.real_gmv(db, '2026-08-23')
    check('整台结账 GMV 增额 = grand_total', abs((after - before) - amt) < 0.01,
          f'delta={after - before} amt={amt}')
    check('8/22 不受影响（仍为 0）', revenue.real_gmv(db, '2026-08-22') == 0,
          str(revenue.real_gmv(db, '2026-08-22')))
    sess = db.execute('SELECT business_date, operator FROM sessions WHERE id=?', [sid]).fetchone()
    check('session.business_date = 8/23', sess['business_date'] == '2026-08-23', str(dict(sess)))
    check('session.operator 已落库（显示名）', sess['operator'] == '管理员', str(dict(sess)))


# ===================== 5. 多人分别结账按各自支付时间归属 =====================

def test_multi_player_attribution():
    print('\n[5] 多人分别结账：各自按支付时间归属营业日')
    CURRENT['now'] = datetime(2026, 8, 22, 23, 59, 0)
    db = get_db()
    g23_before = revenue.real_gmv(db, '2026-08-23')
    start = '2026-08-22T20:00:00'
    sid = start_session(4, start, [
        {'name': '夜猫子A', 'start_time': start},
        {'name': '夜猫子B', 'start_time': start},
    ])
    sps = client.get(f'/api/sessions/{sid}/preview').get_json()['players']
    spA, spB = sps[0]['id'], sps[1]['id']

    # A 在 8/22 23:59 结账
    ra = checkout_player(sid, spA)
    amtA = round(ra['grand_total'], 2)
    check('A 归 8/22', abs(revenue.real_gmv(db, '2026-08-22') - amtA) < 0.01,
          f"g22={revenue.real_gmv(db, '2026-08-22')} amtA={amtA}")

    # B 在 8/23 00:30 结账
    CURRENT['now'] = datetime(2026, 8, 23, 0, 30, 0)
    rb = checkout_player(sid, spB)
    amtB = round(rb['grand_total'], 2)
    g23_after = revenue.real_gmv(db, '2026-08-23')
    check('B 归 8/23', abs(g23_after - (g23_before + amtB)) < 0.01,
          f'g23={g23_after} expect={g23_before + amtB}')

    rowA = db.execute('SELECT business_date FROM session_players WHERE id=?', [spA]).fetchone()
    rowB = db.execute('SELECT business_date FROM session_players WHERE id=?', [spB]).fetchone()
    check('A.business_date = 8/22', rowA['business_date'] == '2026-08-22', str(dict(rowA)))
    check('B.business_date = 8/23', rowB['business_date'] == '2026-08-23', str(dict(rowB)))

    sess = db.execute('SELECT status FROM sessions WHERE id=?', [sid]).fetchone()
    check('末人结账后自动关台', sess['status'] == 'closed', str(dict(sess)))

    # 不重复计数：本 session 在 8/23 只应有 B 一行（A 归 8/22）
    rows23 = [r for r in revenue.iter_real_settlements(db, '2026-08-23') if r['session_id'] == sid]
    check('本桌 8/23 流水行数 = 1（仅 B，无重复）', len(rows23) == 1, f'n={len(rows23)}')


# ===================== 6. 穿透一致 =====================

def test_penetration_consistency():
    print('\n[6] 穿透一致：分析中心 == 收银流水 == revenue.real_gmv')
    # 把分析中心的“今天”固定为 8/23（analytics 用 date.today()，单独 patch _today/_now）
    A._today = lambda: date(2026, 8, 23)
    A._now = lambda: datetime(2026, 8, 23, 12, 0, 0)
    CURRENT['now'] = datetime(2026, 8, 23, 7, 0, 0)
    db = get_db()

    # 函数级 DB 隔离下每测试独立库，本测试需自行造数（不再依赖同文件其他测试的共享库）：
    # 1 个今日已结桌（business_date=8/23）+ 1 个进行中桌（active_sessions=1）
    sid_closed = start_session(1, '2026-08-23T03:00:00', [{'name': '已结桌', 'start_time': '2026-08-23T03:00:00'}])
    sp_id = client.get(f'/api/sessions/{sid_closed}/preview').get_json()['players'][0]['id']
    checkout_player(sid_closed, sp_id)
    start_session(2, '2026-08-23T05:00:00', [{'name': '进行中桌', 'start_time': '2026-08-23T05:00:00'}])

    r = client.get('/api/cashier_history?preset=today')
    assert r.status_code == 200, r.data
    data = r.get_json()['data']
    total_cashier = round(data['totals']['real_paid'], 2)
    check('收银流水实收合计 > 0', total_cashier > 0, f'total={total_cashier}')

    gmv_rev = revenue.real_gmv(db, '2026-08-23')
    check('流水合计 == revenue.real_gmv', abs(total_cashier - gmv_rev) < 0.01,
          f'{total_cashier} vs {gmv_rev}')

    dash = A.get_dashboard(db, 'today')
    gmv_dash = round(dash['business']['today_gmv'], 2)
    check('分析中心 today_gmv == 流水合计（穿透一致）', abs(gmv_dash - total_cashier) < 0.01,
          f'{gmv_dash} vs {total_cashier}')
    check('分析中心 today_observed = GMV + 进行中预计',
          abs(dash['business']['today_observed'] - round(gmv_dash + dash['business']['today_in_progress'], 2)) < 0.01,
          str(dash['business']))
    check('分析中心 today_closed_sessions > 0', dash['business']['today_closed_sessions'] > 0,
          str(dash['business']['today_closed_sessions']))
    check('分析中心 active_sessions = 1', dash['business']['active_sessions'] == 1,
          str(dash['business']['active_sessions']))

    # 自定义日期穿透：/cashier_history?custom_start=8/22&custom_end=8/22
    r22 = client.get('/api/cashier_history?preset=custom&custom_start=2026-08-22&custom_end=2026-08-22')
    d22 = r22.get_json()['data']
    check('8/22 流水合计 == 8/22 GMV（A 的金额）',
          abs(d22['totals']['real_paid'] - revenue.real_gmv(db, '2026-08-22')) < 0.01,
          f"{d22['totals']['real_paid']} vs {revenue.real_gmv(db, '2026-08-22')}")


if __name__ == '__main__':
    print('=' * 60)
    print('QCOS V1.6 营业日口径测试')
    print('=' * 60)
    login()
    test_split()
    test_cross_midnight_attribution()
    test_active_excluded_from_gmv()
    test_whole_table_close()
    test_multi_player_attribution()
    test_penetration_consistency()
    print('\n' + '=' * 60)
    print(f'结果：通过 {PASS} / 失败 {FAIL}')
    print('=' * 60)
    db = get_db()
    db.close()
    try:
        if os.path.exists(_tmp.name):
            os.remove(_tmp.name)
    except OSError:
        pass
    sys.exit(1 if FAIL else 0)
