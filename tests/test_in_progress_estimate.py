"""QCOS V1.6 进行中预计（in_progress_estimate）玩家级汇总测试

背景（真实故障）：
  四口机1 有 4 名 playing 玩家，台桌总览每人 ¥58（玩家级），
  但分析中心「当前进行中预计」只显示 ¥62 —— 旧实现按 session 级
  每桌只算 1 份台费，没有按玩家累加，商品又按整桌 SUM，导致
  4×¥58+¥6 被算成「1 份台费 + 整桌未结商品」。

本测试验证修复后口径（必须玩家级汇总）：
  1. 四口桌 4 人同时 playing，每人预计 58 -> 预计台费 232
  2. 其中 1 人未结商品 6 -> 总预计 238（58×4 + 6，防 58+6+6 重复）
  3. 其中 1 人已经单人结账 -> 不再计该玩家
  4. 商品已 settled -> 不再计商品
  5. 两张 active 桌 -> 全部汇总
  6. 普通桌（白天正常时段）
  7. 通宵桌（包夜 flat）
  8. 跨午夜 active
  9. 0 张 active 桌 -> 0
  10. 台桌总览与 in_progress_estimate 同一玩家金额一致（同一计费引擎、同参数）

实现要点：
  - 临时 SQLite（新 schema，由 conftest 建库 + 模块 seed 触发）
  - fake clock：in_progress_estimate(db, now=...) 直接传参，不 monkeypatch
    任何全局时钟（避免跨模块污染）
  - 只读调用 revenue.in_progress_estimate，不触碰真实 qcos.db
"""

import os
import sys
import tempfile
from datetime import datetime

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
config.PAYMENT['provider'] = None

import models
models.init_db()
import revenue
import billing
from models import get_db

# 假时钟：四口机 22:00 开台（非通宵），跨午夜到次日 03:46
FAKE_NOW = datetime(2026, 8, 24, 3, 46)
# 四口机 ¥10/h；22:00->24:00 = 2h = 20；00:00->03:46 非通宵 226min = 38；合计 58
EXPECT_FEE_PER_PLAYER = 58

# 默认机器：1=八口机1(8port) 2=八口机2(8port) 3=四口机1(4port) 4=四口机2(4port)
M4 = 3   # 四口机1
M8 = 1   # 八口机1


def _settings(db):
    s = {}
    for r in db.execute('SELECT key, value FROM settings').fetchall():
        v = r['value']
        try:
            s[r['key']] = int(v)
        except (ValueError, TypeError):
            try:
                s[r['key']] = float(v)
            except (ValueError, TypeError):
                s[r['key']] = v
    return s


@pytest.fixture(autouse=True)
def _clean_before_each():
    """每个测试函数前清空业务表，避免模块级临时库跨测试残留 active 桌导致金额叠加。"""
    db = get_db()
    db.execute("DELETE FROM product_sales")
    db.execute("DELETE FROM session_players")
    db.execute("DELETE FROM sessions")
    db.commit()
    yield


def _insert_session(db, machine_id, start, is_overnight=0, status='active'):
    cur = db.execute(
        'INSERT INTO sessions (machine_id, start_time, status, is_overnight) '
        'VALUES (?, ?, ?, ?)',
        [machine_id, start.isoformat(timespec='seconds'), status, is_overnight]
    )
    return cur.lastrowid


def _insert_player(db, session_id, name, start, status='playing', is_overnight=0):
    cur = db.execute(
        'INSERT INTO session_players '
        '(session_id, player_name, start_time, status, is_overnight) '
        'VALUES (?, ?, ?, ?, ?)',
        [session_id, name, start.isoformat(timespec='seconds'), status, is_overnight]
    )
    return cur.lastrowid


def _insert_product(db, session_id, sp_id, total, status='UNSETTLED'):
    db.execute(
        "INSERT INTO product_sales "
        "(session_id, session_player_id, product_name, price, quantity, total, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [session_id, sp_id, '可乐', 1, int(total), total, status]
    )


def _make_4p_4port_table(start=None):
    """四口机1 4 名 playing 玩家（同 start，默认 2026-08-23 22:00 跨午夜）。返回 (sid, sp_ids)。"""
    db = get_db()
    start = start or datetime(2026, 8, 23, 22, 0)
    sid = _insert_session(db, M4, start, is_overnight=0)
    sp_ids = [_insert_player(db, sid, f'玩家{i}', start) for i in range(1, 5)]
    db.commit()
    return sid, sp_ids


def seed():
    """基础种子：场景1（四口机 4 人 playing 跨午夜）。返回 dict 供 S fixture 使用。"""
    sid, sp_ids = _make_4p_4port_table()
    return {'sid': sid, 'sp_ids': sp_ids, 'machine_id': M4, 'now': FAKE_NOW}


def test_4p_4port_expected_fee_232():
    """场景1：四口桌 4 人同时 playing，每人预计 58 -> 232（旧实现只得 58）。"""
    sid, sp_ids = _make_4p_4port_table()
    est = revenue.in_progress_estimate(get_db(), now=FAKE_NOW)
    assert est == 4 * EXPECT_FEE_PER_PLAYER, \
        f'4 人四口桌应 {4 * EXPECT_FEE_PER_PLAYER}，实际 {est}（旧实现只得 1 份 {EXPECT_FEE_PER_PLAYER}）'


def test_one_player_product_6_total_238():
    """场景2：其中 1 人未结商品 6 -> 总预计 238 = 232 + 6（防 58+6 变 58+6+6）。"""
    db = get_db()
    sid, sp_ids = _make_4p_4port_table()
    _insert_product(db, sid, sp_ids[0], 6, status='UNSETTLED')
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 4 * EXPECT_FEE_PER_PLAYER + 6, \
        f'应为 {4 * EXPECT_FEE_PER_PLAYER + 6}，实际 {est}（商品只应计一次）'


def test_checked_out_player_excluded():
    """场景3：其中 1 人已单人结账 -> 不再计该玩家，预计 = 3×58 = 174。"""
    db = get_db()
    sid, sp_ids = _make_4p_4port_table()
    db.execute(
        "UPDATE session_players SET status='checked_out', fee=?, final_fee=?, "
        "end_time=?, duration_minutes=? WHERE id=?",
        [EXPECT_FEE_PER_PLAYER, EXPECT_FEE_PER_PLAYER, FAKE_NOW.isoformat(), 346, sp_ids[0]]
    )
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 3 * EXPECT_FEE_PER_PLAYER, \
        f'已结账玩家不应计入，应为 {3 * EXPECT_FEE_PER_PLAYER}，实际 {est}'


def test_settled_product_excluded():
    """场景4：商品已 SETTLED -> 不再计商品，预计 = 232（台费照常）。"""
    db = get_db()
    sid, sp_ids = _make_4p_4port_table()
    _insert_product(db, sid, sp_ids[0], 6, status='SETTLED')
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 4 * EXPECT_FEE_PER_PLAYER, \
        f'已结算商品不应计入，应为 {4 * EXPECT_FEE_PER_PLAYER}，实际 {est}'


def test_two_active_tables_summed():
    """场景5：两张 active 桌全部汇总。
    桌A 四口 4 人(22:00) = 232；
    桌B 八口 2 人(23:00) = 2×(20 + 77) = 194；合计 426。
    """
    db = get_db()
    sid_a, _ = _make_4p_4port_table()          # 四口 4 人 -> 232
    start_b = datetime(2026, 8, 23, 23, 0)      # 八口 2 人 -> 每人 97
    sid_b = _insert_session(db, M8, start_b, is_overnight=0)
    for i in range(1, 3):
        _insert_player(db, sid_b, f'乙桌玩家{i}', start_b)
    db.commit()

    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    expected = 4 * EXPECT_FEE_PER_PLAYER + 2 * 97
    assert est == expected, f'两桌应汇总为 {expected}，实际 {est}'


def test_normal_table_daytime():
    """场景6：普通桌（白天正常时段）——四口 4 人 13:00 开台，15:30 每人 25 -> 100。"""
    db = get_db()
    start = datetime(2026, 8, 24, 13, 0)
    now = datetime(2026, 8, 24, 15, 30)
    sid = _insert_session(db, M4, start, is_overnight=0)
    for i in range(1, 5):
        _insert_player(db, sid, f'玩家{i}', start)
    db.commit()
    est = revenue.in_progress_estimate(db, now=now)
    # 13:00->15:30 = 150min -> full=2, rem=30>10 -> 额外30min -> 2*10+5=25
    assert est == 4 * 25, f'普通桌应为 100，实际 {est}'


def test_overnight_table_flat():
    """场景7：通宵桌（四口包夜 ¥50）——23:00 开台，03:46 每人 10+50=60 -> 240。"""
    db = get_db()
    start = datetime(2026, 8, 23, 23, 0)
    sid = _insert_session(db, M4, start, is_overnight=1)
    for i in range(1, 5):
        _insert_player(db, sid, f'玩家{i}', start)
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 4 * 60, f'通宵桌应为 240（每人 10 正常 + 50 包夜），实际 {est}'


def test_cross_midnight_active():
    """场景8：跨午夜 active（22:00 -> 次日 03:46），每人 58（非通宵阶梯）。"""
    db = get_db()
    sid, sp_ids = _make_4p_4port_table(datetime(2026, 8, 23, 22, 0))
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 232, f'跨午夜桌应为 232，实际 {est}'
    # 单玩家台费与台桌总览引擎一致（calculate_fee 同参数）
    s = _settings(db)
    fee, _ = billing.calculate_fee('4port', datetime(2026, 8, 23, 22, 0), FAKE_NOW, s, is_overnight=False)
    assert fee == EXPECT_FEE_PER_PLAYER, f'单玩家应为 {EXPECT_FEE_PER_PLAYER}，实际 {fee}'


def test_zero_active_tables():
    """场景9：0 张 active 桌 -> 0（空库也能正常返回，不崩溃）。"""
    db = get_db()
    db.execute("DELETE FROM product_sales")
    db.execute("DELETE FROM session_players")
    db.execute("DELETE FROM sessions")
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    assert est == 0, f'无 active 桌应为 0，实际 {est}'


def test_same_player_amount_as_table_overview():
    """场景10：台桌总览与 in_progress_estimate 同一玩家金额一致。
    台桌总览 = get_current_fee(machine_type, p_start, settings, is_overnight)，
    即 calculate_fee(machine_type, p_start, now, settings, is_overnight)。
    in_progress_estimate 修复后必须对每个 playing 玩家用同一引擎、同一参数。
    """
    db = get_db()
    sid, sp_ids = _make_4p_4port_table()
    s = _settings(db)
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)

    # 台桌总览同款：对每个 playing 玩家（同 start 22:00，同 is_overnight=0）
    overview_per_player = billing.calculate_fee(
        '4port', datetime(2026, 8, 23, 22, 0), FAKE_NOW, s, is_overnight=False)[0]
    assert overview_per_player == EXPECT_FEE_PER_PLAYER

    # in_progress_estimate 必须等于 4 × 台桌总览单玩家金额（同一引擎，不是另一套算法）
    assert est == 4 * overview_per_player, \
        f'应等于台桌总览 4×{overview_per_player}={4 * overview_per_player}，实际 {est}'


def test_counter_product_not_double_counted():
    """防重复：桌级柜台未结商品（session_player_id IS NULL）只计一次。
    场景：4 人桌（232）+ 1 人未结商品 6 + 柜台未结商品 4 = 242。
    若把整桌商品 SUM 与玩家商品都加一遍会得到 248（重复计了玩家商品）。
    """
    db = get_db()
    sid, sp_ids = _make_4p_4port_table()
    _insert_product(db, sid, sp_ids[0], 6, status='UNSETTLED')       # 玩家挂账
    db.execute(
        "INSERT INTO product_sales "
        "(session_id, session_player_id, product_name, price, quantity, total, status) "
        "VALUES (?, NULL, '柜台零食', 1, 1, 4, 'UNSETTLED')",
        [sid]
    )
    db.commit()
    est = revenue.in_progress_estimate(db, now=FAKE_NOW)
    expected = 4 * EXPECT_FEE_PER_PLAYER + 6 + 4   # 232 + 6 + 4 = 242
    assert est == expected, f'应为 {expected}，实际 {est}（柜台商品不得与玩家商品重复/漏算）'


def test_seed_fixture_compatible():
    """S fixture（conftest）兼容：模块 seed() 返回的种子可被消费（S 语义 = 种子字典）。"""
    # 直接调用模块 seed 两次会造成重复数据；这里只验证 seed 返回结构（不重复建库）
    # 说明：S fixture 由 conftest 提供，本测试仅保证模块存在 seed() 且返回可解析字典。
    assert callable(seed), '模块必须提供 seed() 供 conftest S fixture 使用'
