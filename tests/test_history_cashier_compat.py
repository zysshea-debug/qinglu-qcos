"""QCOS V1.6.1 历史收银流水兼容测试

背景（门店真实库只读审计）：
  - 74 条 checked_out 玩家 business_date=NULL / paid_at=NULL（V1.6 之前原生结账）
    -> 当前 V1.6 收银流水因 business_date IS NOT NULL 硬过滤全部不可见（GAP）。
  - legacy Excel 导入写 sessions.status='completed'，而部分查询只认 'closed'，
    存在叠加缺口。
  - 门店进行中：四口机1 4 名 playing 每人 65，salty 未结商品 6
    -> PLAYER_LEVEL_TOTAL=258，但旧逻辑 in_progress_estimate=69。

本测试验证 V1.6.1 兼容层：
  1. pre-V16 checked_out + 无 business_date -> 可见（NATIVE_PRE_V16）
  2. 不伪造 paid_at（paid_at=None，display_time=归档时间）
  3. player end_time 归档（date_source=PLAYER_END_TIME）
  4. 跨午夜 8/22 开 8/23 结束 -> 8/23 查询可见
  5. grand_total 有值 -> VISIBLE_EXACT
  6. grand_total NULL + final_fee 可信 -> SP_FINAL_FEE（VISIBLE_PARTIAL）
  7. 完全无可信金额 -> VISIBLE_INCOMPLETE（不伪造 0 元）
  8. payment_method 缺失 -> 历史/未知
  9. legacy completed -> 可见
  10. legacy closed -> 兼容
  11. legacy date_only -> 可见
  12. native + legacy 不重复
  13. visit_record / payment + native 不重复
  14. 昨日 / 近7天 / 本月 / 自定义筛选
  15. Excel 导出带 source_type / visibility_state
  16. 桌局汇总不重复
  17. void V1.6 记录仍正常排除
  18. 历史记录不会被无声过滤（每条至少 VISIBLE_*）
  19. 历史兼容不计入真实 GMV（GMV 口径与可见性分离）
  20. in_progress_estimate 4×63+6 = 258（玩家级修复保留；63 为 BUG A 连续计费正确值，旧 62 为拆段取整副产物）

安全：临时 SQLite（conftest 建库）+ API 只读写临时库，不触碰真实 qcos.db。
"""

import os
import sys
import io
import sqlite3
import tempfile
from datetime import datetime, date, timedelta

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import config
import models

_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name
# 关键：models 可能已被前序测试模块缓存导入（models.DB_PATH 是 `from config import DB_PATH`
# 的陈旧绑定）。若不同步覆盖，模块级 `DB = get_db()` 会连到前序模块的临时库 →
# 数据层用例写旧库、API 用例读本模块库 → 全量回归时 API 日期过滤全空（顺序依赖失败）。
models.DB_PATH = _tmp.name
config.PAYMENT['provider'] = None

models.init_db()
import app as APP
import revenue
from models import get_db

# 告知 conftest：本模块每测试用模块级 DB 直写、并依赖 API 读同一库，
# 需要在每测试把 DB 重新绑定到 conftest 的临时库（DB 与 API 同源）。
_REBIND_DB_PER_TEST = True

client = APP.app.test_client()

# 模块级单一连接：避免每个测试新建连接后残留 SQLite 读事务（SHARED 锁）
# 阻塞后续测试的 DELETE（database is locked）
DB = get_db()

TODAY = date.today()
# 门店现场场景：四口机1 4 名 playing，每人 65，salty 未结商品 6 -> 266
FAKE_NOW = datetime(2026, 8, 24, 3, 46)
START_65 = datetime(2026, 8, 23, 21, 20)   # 四口非通宵 -> 03:46 每人 65
M4 = 3   # 四口机1


def login():
    r = client.post('/api/auth/login', json={
        'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    assert r.status_code == 200, r.data


def _clean():
    # 先删引用表（外键），再删 sessions；表不存在则跳过
    for t in ['product_sales', 'session_players', 'visit_records', 'payments',
              'end_time_audit', 'session_feedback', 'cashier_audit', 'sessions']:
        try:
            DB.execute('DELETE FROM %s' % t)
        except sqlite3.OperationalError:
            pass
    DB.commit()


@pytest.fixture(autouse=True)
def _clean_before_each():
    _clean()
    yield


def _insert_session(db, machine_id, start, end=None, status='closed', final_fee=None,
                    source_id=None, time_precision=None, payment_method=None,
                    is_overnight=0, business_date=None, paid_at=None):
    if end is None and status != 'active':
        end = start
    end_str = end.isoformat(timespec='seconds') if end else None
    cur = db.execute(
        '''INSERT INTO sessions
           (machine_id, start_time, end_time, status, final_fee, source_id,
            time_precision, payment_method, is_overnight, business_date, paid_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        [machine_id, start.isoformat(timespec='seconds'), end_str,
         status, final_fee, source_id, time_precision, payment_method, is_overnight,
         business_date, paid_at])
    return cur.lastrowid


def _insert_player(db, sid, name, start, end, grand_total=None, final_fee=None,
                   product_total=0, payment_method=None, status='checked_out',
                   business_date=None, paid_at=None, player_id=None):
    cur = db.execute(
        '''INSERT INTO session_players
           (session_id, player_name, player_id, start_time, end_time, fee,
            final_fee, product_total, grand_total, payment_method, status,
            business_date, paid_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        [sid, name, player_id, start.isoformat(timespec='seconds'),
         end.isoformat(timespec='seconds'), None, final_fee, product_total,
         grand_total, payment_method, status, business_date, paid_at])
    return cur.lastrowid


def seed():
    """conftest S fixture 兼容：模块 seed() 存在即可（测试各自造数）。"""
    return {}


# ===================== 数据层：NATIVE_PRE_V16 =====================

def test_pre_v16_checked_out_visible():
    """场景1：pre-V16 checked_out + 无 business_date -> 收银流水可见。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 22, 17, 30),
                          end=datetime(2026, 8, 23, 5, 35), final_fee=190)
    _insert_player(db, sid, '老王', datetime(2026, 8, 22, 17, 30),
                   datetime(2026, 8, 23, 5, 35), grand_total=190, payment_method='cash')
    db.commit()
    rows = revenue.iter_settlement_rows(db)
    pre = [r for r in rows if r['source_type'] == 'NATIVE_PRE_V16']
    assert len(pre) == 1, f'应恰好 1 条 NATIVE_PRE_V16，实际 {len(pre)}'
    assert pre[0]['session_id'] == sid
    assert pre[0]['kind'] == 'player'


def test_paid_at_not_forged():
    """场景2：pre-V16 无 paid_at -> 不伪造，paid_at=None + display_time=归档时间。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 22, 17, 30),
                          end=datetime(2026, 8, 23, 5, 35), final_fee=190)
    _insert_player(db, sid, '老王', datetime(2026, 8, 22, 17, 30),
                   datetime(2026, 8, 23, 5, 35), grand_total=190)
    db.commit()
    row = [r for r in revenue.iter_settlement_rows(db)
           if r['source_type'] == 'NATIVE_PRE_V16'][0]
    assert row['paid_at'] is None, '不得伪造 paid_at'
    assert row['display_time'] == '2026-08-23T05:35:00', row['display_time']
    assert row['historical_compat'] == 1


def test_player_end_time_archival():
    """场景3：按 player end_time 归档（非 start_time）。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 22, 17, 30),
                          end=datetime(2026, 8, 23, 5, 35), final_fee=190)
    _insert_player(db, sid, '老王', datetime(2026, 8, 22, 17, 30),
                   datetime(2026, 8, 23, 5, 35), grand_total=190)
    db.commit()
    row = [r for r in revenue.iter_settlement_rows(db)
           if r['source_type'] == 'NATIVE_PRE_V16'][0]
    assert row['date_source'] == 'PLAYER_END_TIME'
    assert row['business_date'] == '2026-08-23', \
        f'应归档到 player end 8/23，实际 {row["business_date"]}'


def test_cross_midnight_visible_on_0823_api():
    """场景4：跨午夜 8/22 开 8/23 结束 -> 自定义 8/23 查询可见。"""
    login()
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 22, 17, 30),
                          end=datetime(2026, 8, 23, 5, 35), final_fee=190)
    _insert_player(db, sid, '老王', datetime(2026, 8, 22, 17, 30),
                   datetime(2026, 8, 23, 5, 35), grand_total=190)
    db.commit()
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23'})
    assert r.status_code == 200, r.data
    rows = r.get_json()['data']['rows']
    assert any(x['session_id'] == sid for x in rows), \
        '8/23 自定义查询应包含该跨午夜桌（按 player end_time 归档）'
    # 8/22 不应包含（不能按 start_time 全算进 8/22）
    r2 = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-22', 'custom_end': '2026-08-22'})
    rows2 = r2.get_json()['data']['rows']
    assert not any(x['session_id'] == sid for x in rows2), \
        '8/22 不应包含该桌（player 8/23 才结束）'


def test_grand_total_exact():
    """场景5：grand_total 有值 -> VISIBLE_EXACT + 金额正确。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=100)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=100, payment_method='cash')
    _insert_player(db, sid, '乙', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=106, payment_method='wechat')
    db.commit()
    rows = [r for r in revenue.iter_settlement_rows(db)
            if r['source_type'] == 'NATIVE_PRE_V16']
    assert len(rows) == 2
    by_name = {r['player_name']: r for r in rows}
    assert by_name['甲']['visibility_state'] == 'VISIBLE_EXACT'
    assert by_name['甲']['amount'] == 100
    assert by_name['甲']['amount_source'] == 'SP_GRAND_TOTAL'
    assert by_name['乙']['amount'] == 106


def test_grand_total_null_final_fee_trusted():
    """场景6：grand_total NULL 但 final_fee 可信 -> SP_FINAL_FEE 来源。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=None)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=None,
                   final_fee=100, product_total=6)
    db.commit()
    row = [r for r in revenue.iter_settlement_rows(db)
           if r['source_type'] == 'NATIVE_PRE_V16'][0]
    assert row['visibility_state'] == 'VISIBLE_PARTIAL'
    assert row['amount_source'] == 'SP_FINAL_FEE'
    assert row['amount'] == 106, f'应取 final_fee+product_total=106，实际 {row["amount"]}'


def test_no_trusted_amount_incomplete():
    """场景7：完全无可信金额 -> VISIBLE_INCOMPLETE（不消失、不伪造 0 元）。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=None)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0))
    db.commit()
    row = [r for r in revenue.iter_settlement_rows(db)
           if r['source_type'] == 'NATIVE_PRE_V16'][0]
    assert row['visibility_state'] == 'VISIBLE_INCOMPLETE'
    assert row['amount'] is None
    assert row['amount_source'] == 'NONE'
    # 不进入「真实实收」合计
    assert row['grand_total'] is None


def test_session_level_amount_when_no_personal():
    """场景7b：该桌所有玩家无个人金额但 session.final_fee 可信 -> 桌级一行不拆。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=200)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0))
    _insert_player(db, sid, '乙', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0))
    db.commit()
    rows = [r for r in revenue.iter_settlement_rows(db)
            if r['source_type'] == 'NATIVE_PRE_V16']
    assert len(rows) == 1, f'应出 1 条桌级行，实际 {len(rows)}'
    assert rows[0]['kind'] == 'session'
    assert rows[0]['amount'] == 200
    assert rows[0]['amount_source'] == 'SESSION_FINAL_FEE'
    assert rows[0]['visibility_state'] == 'VISIBLE_PARTIAL'


def test_payment_method_missing_shows_unknown():
    """场景8：payment_method 缺失 -> 历史/未知（不猜现金/微信/支付宝）。"""
    login()
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=100)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=100)
    db.commit()
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23'})
    rows = r.get_json()['data']['rows']
    row = [x for x in rows if x['session_id'] == sid][0]
    assert row['payment_label'] == '历史/未知'
    assert row['payment_method'] is None


# ===================== 数据层：LEGACY_IMPORT =====================

def test_legacy_completed_visible():
    """场景9：legacy status='completed' -> 可见。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 15, 12, 0),
                          end=datetime(2026, 8, 15, 15, 0), status='completed',
                          final_fee=120, source_id='excel_2026-08-15-1')
    db.commit()
    rows = revenue.iter_settlement_rows(db)
    leg = [r for r in rows if r['source_type'] == 'LEGACY_IMPORT']
    assert len(leg) == 1
    assert leg[0]['session_id'] == sid
    assert leg[0]['visibility_state'] == 'VISIBLE_PARTIAL'
    assert leg[0]['amount'] == 120


def test_legacy_closed_compatible():
    """场景10：legacy status='closed' -> 兼容可见。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 16, 12, 0),
                          end=datetime(2026, 8, 16, 15, 0), status='closed',
                          final_fee=90, source_id='excel_2026-08-16-1')
    db.commit()
    rows = [r for r in revenue.iter_settlement_rows(db)
            if r['source_type'] == 'LEGACY_IMPORT']
    assert len(rows) == 1 and rows[0]['session_id'] == sid


def test_legacy_date_only_visible():
    """场景11：legacy time_precision='date_only'（无 source_id）-> 可见。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 17, 12, 0),
                          end=datetime(2026, 8, 17, 15, 0), status='completed',
                          final_fee=80, time_precision='date_only')
    db.commit()
    rows = [r for r in revenue.iter_settlement_rows(db)
            if r['source_type'] == 'LEGACY_IMPORT']
    assert len(rows) == 1 and rows[0]['session_id'] == sid


def test_native_plus_legacy_no_dup():
    """场景12：legacy 桌（含 checked_out players）只出 1 行，不重复进 pre_v16。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 15, 12, 0),
                          end=datetime(2026, 8, 15, 15, 0), status='completed',
                          final_fee=120, source_id='excel_2026-08-15-1')
    _insert_player(db, sid, '甲', datetime(2026, 8, 15, 12, 0),
                   datetime(2026, 8, 15, 15, 0), grand_total=60)
    _insert_player(db, sid, '乙', datetime(2026, 8, 15, 12, 0),
                   datetime(2026, 8, 15, 15, 0), grand_total=60)
    db.commit()
    rows = revenue.iter_settlement_rows(db)
    leg = [r for r in rows if r['source_type'] == 'LEGACY_IMPORT' and r['session_id'] == sid]
    pre = [r for r in rows if r['source_type'] == 'NATIVE_PRE_V16' and r['session_id'] == sid]
    assert len(leg) == 1, 'legacy 桌只能出 1 条'
    assert len(pre) == 0, 'legacy 桌不得再按 NATIVE_PRE_V16 重复出玩家行'


def test_visit_record_and_payment_no_dup():
    """场景13/14：visit_records / payments 存在不产生重复收入行。"""
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=100)
    sp1 = _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                         datetime(2026, 8, 23, 15, 0), grand_total=100)
    db.execute(
        "INSERT INTO visit_records (player_id, player_name, visit_date, payment_amount, session_id) "
        "VALUES (?, ?, ?, ?, ?)", [None, '甲', '2026-08-23', 100.0, sid])
    db.execute(
        "INSERT INTO payments (out_trade_no, method, amount, status, provider, session_player_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ['TEST-NODUP-1', 'cash', 100.0, 'SUCCESS', 'test', sp1,
         datetime(2026, 8, 23, 15, 0).isoformat(), datetime(2026, 8, 23, 15, 0).isoformat()])
    db.commit()
    rows = [r for r in revenue.iter_settlement_rows(db)
            if r['source_type'] == 'NATIVE_PRE_V16' and r['session_id'] == sid]
    assert len(rows) == 1, f'visit_record+payment 不得产生重复行，实际 {len(rows)}'


# ===================== 时间筛选（API） =====================

def test_yesterday_filter():
    """场景15：昨日筛选包含 pre-V16 旧记录。"""
    login()
    db = DB
    yd = date.today() - timedelta(days=1)
    end = datetime(yd.year, yd.month, yd.day, 22, 0)
    sid = _insert_session(db, M4, datetime(yd.year, yd.month, yd.day, 18, 0), end=end, final_fee=50)
    _insert_player(db, sid, '甲', datetime(yd.year, yd.month, yd.day, 18, 0), end, grand_total=50)
    db.commit()
    r = client.get('/api/cashier_history', query_string={'preset': 'yesterday'})
    rows = r.get_json()['data']['rows']
    assert any(x['session_id'] == sid for x in rows), '昨日视图应包含 pre-V16 记录'


def test_7d_filter():
    """场景16：近7天包含 pre-V16 旧记录。"""
    login()
    db = DB
    d = date.today() - timedelta(days=3)
    end = datetime(d.year, d.month, d.day, 22, 0)
    sid = _insert_session(db, M4, datetime(d.year, d.month, d.day, 18, 0), end=end, final_fee=50)
    _insert_player(db, sid, '甲', datetime(d.year, d.month, d.day, 18, 0), end, grand_total=50)
    db.commit()
    r = client.get('/api/cashier_history', query_string={'preset': '7d'})
    rows = r.get_json()['data']['rows']
    assert any(x['session_id'] == sid for x in rows), '近7天视图应包含 pre-V16 记录'


def test_month_filter_includes_legacy_and_pre_v16():
    """场景17：本月包含 pre-V16 与 legacy 记录。"""
    login()
    db = DB
    # pre-v16
    d1 = date.today()
    # 用「不晚于今天」的日期，避免今天处于月初时 day=5/6 落在 month 视图（end=today）之外
    day_a = d1.day
    day_b = max(1, d1.day - 1)
    sid1 = _insert_session(db, M4, datetime(d1.year, d1.month, day_a, 18, 0),
                           end=datetime(d1.year, d1.month, day_a, 21, 0), final_fee=50)
    _insert_player(db, sid1, '甲', datetime(d1.year, d1.month, day_a, 18, 0),
                   datetime(d1.year, d1.month, day_a, 21, 0), grand_total=50)
    # legacy completed
    sid2 = _insert_session(db, M4, datetime(d1.year, d1.month, day_b, 18, 0),
                           end=datetime(d1.year, d1.month, day_b, 21, 0), status='completed',
                           final_fee=120, source_id='excel_month_test')
    db.commit()
    r = client.get('/api/cashier_history', query_string={'preset': 'month'})
    rows = r.get_json()['data']['rows']
    sids = {x['session_id'] for x in rows}
    assert sid1 in sids and sid2 in sids, '本月应同时包含 pre-V16 与 legacy'


def test_custom_date_filter_0821():
    """场景18：自定义 8/21 精确查询（门店审计日）。"""
    login()
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 21, 10, 0),
                          end=datetime(2026, 8, 21, 13, 0), final_fee=30)
    _insert_player(db, sid, '甲', datetime(2026, 8, 21, 10, 0),
                   datetime(2026, 8, 21, 13, 0), grand_total=30)
    db.commit()
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-21', 'custom_end': '2026-08-21'})
    rows = r.get_json()['data']['rows']
    assert any(x['session_id'] == sid for x in rows)


# ===================== 汇总 / 导出 / void / GMV 分离 =====================

def test_session_summary_no_dup():
    """场景20：桌局汇总每桌一行，金额 = 玩家合计（不重复）。"""
    login()
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=None)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=100)
    _insert_player(db, sid, '乙', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=106)
    db.commit()
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23'})
    data = r.get_json()['data']
    rows = data['rows']
    assert len([x for x in rows if x['session_id'] == sid]) == 2
    summ = [s for s in data['session_summary'] if s['session_id'] == sid]
    assert len(summ) == 1, f'桌局汇总应 1 行，实际 {len(summ)}'
    assert summ[0]['grand_total'] == 206, f'桌局合计应为 206，实际 {summ[0]["grand_total"]}'


def test_export_has_source_columns():
    """场景19：Excel 导出带 source_type / visibility_state 等列。"""
    login()
    from openpyxl import load_workbook
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=100)
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=100)
    db.commit()
    r = client.get('/api/cashier_history/export', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23'})
    assert r.status_code == 200, r.data
    wb = load_workbook(io.BytesIO(r.data))
    ws = wb['玩家收银流水']
    headers = [c.value for c in ws[1]]
    for col in ['来源类型', '历史兼容', '可见状态', '日期来源', '金额来源']:
        assert col in headers, f'导出应含列 {col}'
    row_vals = [c.value for c in ws[2]]
    row_map = dict(zip(headers, row_vals))
    assert row_map['来源类型'] == 'NATIVE_PRE_V16'
    assert row_map['历史兼容'] == '是'
    assert row_map['可见状态'] == 'VISIBLE_EXACT'
    assert row_map['日期来源'] == 'PLAYER_END_TIME'


def test_void_v16_still_excluded():
    """场景21：void V1.6 记录在 normal 视图排除、voided 视图可见。"""
    login()
    db = DB
    sid = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                          end=datetime(2026, 8, 23, 15, 0), final_fee=100,
                          business_date='2026-08-23', paid_at='2026-08-23T15:00:00')
    _insert_player(db, sid, '甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=100,
                   business_date='2026-08-23', paid_at='2026-08-23T15:00:00')
    db.commit()
    # 正常视图可见（NATIVE_V16）
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23'})
    assert any(x['session_id'] == sid for x in r.get_json()['data']['rows'])
    # 作废
    revenue.void_session(db, sid, 'TEST_DATA', 'tester')
    db.commit()
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23',
        'void': 'normal'})
    assert not any(x['session_id'] == sid for x in r.get_json()['data']['rows']), \
        '作废后 normal 视图必须排除'
    r = client.get('/api/cashier_history', query_string={
        'preset': 'custom', 'custom_start': '2026-08-23', 'custom_end': '2026-08-23',
        'void': 'voided'})
    assert any(x['session_id'] == sid for x in r.get_json()['data']['rows']), \
        '作废后 voided 视图应可见'


def test_no_record_silently_dropped():
    """场景22：任何已结账历史记录都至少 VISIBLE_*，禁止 INVISIBLE。"""
    db = DB
    # exact / partial / incomplete / legacy / completed
    s1 = _insert_session(db, M4, datetime(2026, 8, 21, 10, 0),
                         end=datetime(2026, 8, 21, 13, 0), final_fee=30)
    _insert_player(db, s1, 'EXACT', datetime(2026, 8, 21, 10, 0),
                   datetime(2026, 8, 21, 13, 0), grand_total=30)
    s2 = _insert_session(db, M4, datetime(2026, 8, 21, 11, 0),
                         end=datetime(2026, 8, 21, 14, 0), final_fee=None)
    _insert_player(db, s2, 'PARTIAL', datetime(2026, 8, 21, 11, 0),
                   datetime(2026, 8, 21, 14, 0), grand_total=None, final_fee=20)
    s3 = _insert_session(db, M4, datetime(2026, 8, 21, 12, 0),
                         end=datetime(2026, 8, 21, 15, 0), final_fee=None)
    _insert_player(db, s3, 'INCOMPLETE', datetime(2026, 8, 21, 12, 0),
                   datetime(2026, 8, 21, 15, 0))
    s4 = _insert_session(db, M4, datetime(2026, 8, 21, 13, 0),
                         end=datetime(2026, 8, 21, 16, 0), status='completed',
                         final_fee=40, source_id='excel_drop_test')
    db.commit()
    rows = revenue.iter_settlement_rows(db)
    allowed = {'VISIBLE_EXACT', 'VISIBLE_PARTIAL', 'VISIBLE_INCOMPLETE'}
    by_sid = {}
    for r in rows:
        by_sid.setdefault(r['session_id'], []).append(r)
    for sid in [s1, s2, s3, s4]:
        assert sid in by_sid, f'session {sid} 不得无声消失'
        for r in by_sid[sid]:
            assert r['visibility_state'] in allowed, \
                f'session {sid} 出现非法可见状态 {r["visibility_state"]}'


def test_history_not_in_real_gmv():
    """GMV 分离：历史兼容记录不计入真实实收 GMV（real_gmv 仅 NATIVE_V16）。"""
    db = DB
    # pre-v16（无 business_date）
    s1 = _insert_session(db, M4, datetime(2026, 8, 23, 12, 0),
                         end=datetime(2026, 8, 23, 15, 0), final_fee=100)
    _insert_player(db, s1, '旧甲', datetime(2026, 8, 23, 12, 0),
                   datetime(2026, 8, 23, 15, 0), grand_total=100)
    # V1.6 新结账（business_date 有值）
    s2 = _insert_session(db, M4, datetime(2026, 8, 23, 16, 0),
                         end=datetime(2026, 8, 23, 18, 0), final_fee=50,
                         business_date='2026-08-23', paid_at='2026-08-23T18:00:00')
    _insert_player(db, s2, '新甲', datetime(2026, 8, 23, 16, 0),
                   datetime(2026, 8, 23, 18, 0), grand_total=50,
                   business_date='2026-08-23', paid_at='2026-08-23T18:00:00')
    db.commit()
    # iter_real_settlements（GMV 视图）必须只有 V1.6 行
    gmv_rows = revenue.iter_real_settlements(db, business_date='2026-08-23')
    assert len(gmv_rows) == 1, f'GMV 视图应只有 1 条（V1.6），实际 {len(gmv_rows)}'
    assert gmv_rows[0]['session_id'] == s2
    assert revenue.real_gmv(db, '2026-08-23') == 50, \
        f'真实 GMV 应为 50（不含 pre-v16 的 100），实际 {revenue.real_gmv(db, "2026-08-23")}'
    # 收银流水视图（iter_settlement_rows）两行都可见（历史可见 ≠ 计入 GMV）
    all_rows = revenue.iter_settlement_rows(db)
    sids = {r['session_id'] for r in all_rows}
    assert s1 in sids and s2 in sids


def test_in_progress_estimate_266():
    """门店现场场景：四口 4 人 playing 每人 63（BUG A 连续计费正确值）+ salty 未结商品 6 -> 258（非 69）。"""
    db = DB
    sid = _insert_session(db, M4, START_65, end=None, status='active', final_fee=None)
    sp_ids = []
    for i, name in enumerate(['玩家1', '玩家2', '玩家3', 'salty'], 1):
        cur = db.execute(
            '''INSERT INTO session_players
               (session_id, player_name, start_time, status, is_overnight)
               VALUES (?, ?, ?, 'playing', 0)''',
            [sid, name, START_65.isoformat(timespec='seconds')])
        sp_ids.append(cur.lastrowid)
    # salty 未结商品 6
    db.execute(
        "INSERT INTO product_sales (session_id, session_player_id, product_name, "
        "price, quantity, total, status) VALUES (?, ?, ?, ?, ?, ?, 'UNSETTLED')",
        [sid, sp_ids[3], '可乐', 1, 6, 6])
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 258, f'4×63+6 应为 258，实际 {est}（旧逻辑只得 69；63 为 BUG A 连续计费正确值）'


def test_in_progress_estimate_same_as_table_overview_266():
    """玩家级口径一致：in_progress_estimate 单人贡献 == 台桌总览引擎同参数金额。"""
    db = DB
    sid = _insert_session(db, M4, START_65, end=None, status='active', final_fee=None)
    for name in ['玩家1', '玩家2', '玩家3', 'salty']:
        db.execute(
            '''INSERT INTO session_players
               (session_id, player_name, start_time, status, is_overnight)
               VALUES (?, ?, ?, 'playing', 0)''',
            [sid, name, START_65.isoformat(timespec='seconds')])
    db.commit()
    import billing
    s = {}
    for row in db.execute('SELECT key, value FROM settings').fetchall():
        v = row['value']
        try:
            s[row['key']] = int(v)
        except (ValueError, TypeError):
            try:
                s[row['key']] = float(v)
            except (ValueError, TypeError):
                s[row['key']] = v
    per = billing.calculate_fee('4port', START_65, FAKE_NOW, s, is_overnight=False)[0]
    assert per == 63, f'台桌总览单玩家应为 63（BUG A 连续计费正确值），实际 {per}'
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 4 * per, f'预计应收应等于 4×台桌总览单玩家金额，实际 {est}'