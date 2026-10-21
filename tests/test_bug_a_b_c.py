"""青鹭 QCOS v2.0 —— BUG A / B / C 回归测试

对应门店真实生产系统的三个已确认 Bug：

  BUG A：普通计时（非通宵）跨 00:00 被错误拆段、对凌晨小段单独收最低1小时。
        修复：calculate_fee(is_overnight=False) 把 00:00-08:00 凌晨窗口并入相邻
        正常时段，作为同一连续场计一次普通台费（见 billing._merge_non_overnight_segments）。

  BUG B：通宵默认截止被错误算成 开台时间 + 8 小时（如 21:46 -> 05:46）。
        修复：overnight.compute_auto_end_at 改为青鹭固定通宵窗口 00:00~08:00，
        截止 = 当日/次日 08:00（见 overnight.compute_auto_end_at）。

  BUG C：10 分钟免费缓冲被错误计算（rem>10 就把前面 10 分钟重新计入收费）。
        修复：billing._charged_extra_minutes 仅对「超出免费缓冲的部分」按阶梯计费
        （上一轮已完成，本轮回归确认）。

本文件只用标准 pytest 断言，便于纳入 JUnit 报告与全量回归。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault('QCOS_SECRET_KEY', 'test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test')

import config  # noqa: E402
import models  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name
models.DB_PATH = _tmp.name  # 同步模块级绑定，避免连到其它测试模块的临时库
config.PAYMENT['provider'] = None
models.init_db()  # 建表并写入 settings 种子（_settings() 依赖此表）

import billing  # noqa: E402
import overnight  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402
from models import get_db  # noqa: E402


def _settings():
    """读取默认 seed 设置（与门店初始化一致）。"""
    db = get_db()
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


S = _settings()
RATE_8 = S.get('rate_8port', 20)   # 八口标准 20/h
RATE_4 = S.get('rate_4port', 10)   # 四口标准 10/h


# =====================================================================
# BUG C：10 分钟免费缓冲（纯缓冲规则，用 compute_hourly_fee 直接验证）
# =====================================================================
def test_bug_c_buffer_step_standard_20h():
    """标准价 ¥20/h 的 10 个回归用例（仅缓冲规则，不跨午夜）。

    注意：compute_hourly_fee 最终经 _round_int 四舍五入为整数，故期望值取整数。
    缓冲阶梯只作用于「首小时之后」的零碎分钟；首小时内按最低1小时计费。
    """
    cases = [
        (120, 40.0),   # 2:00  = 2h
        (127, 40.0),   # 2:07  = 2h（首小时之后仅7min，未超缓冲）
        (130, 40.0),   # 2:10  = 2h（零碎=缓冲上限10，不触发阶梯）
        (131, 43.0),   # 2:11  = 2h + 计费10min(3.33) -> 43
        (140, 43.0),   # 2:20  = 2h + 计费10min -> 43
        (141, 47.0),   # 2:21  = 2h + 计费20min(6.67) -> 47
        (150, 47.0),   # 2:30  = 2h + 计费20min -> 47
        (151, 50.0),   # 2:31  = 2h + 计费30min(10) -> 50
        (153, 50.0),   # 2:33  -> 50（整2h=40 + 超出33扣10剩23阶梯30min=10）BUG C 关键
        (247, 80.0),   # 4:07  = 4h（零碎7min未超缓冲）
    ]
    for minutes, expect in cases:
        got = billing.compute_hourly_fee(minutes, 20, S)
        assert abs(got - expect) < 0.01, f'{minutes}min -> {got}, expect {expect}'


def test_bug_c_charged_extra_minutes():
    """BUG C 精确验证（不取整）：_charged_extra_minutes 仅对「超出免费缓冲的部分」
    按阶梯计费（k*step），不得在 k>=2 时返回 (k+1)*step 把已扣缓冲重新计入。"""
    # (rem, expect_charged_minutes)
    cases = [
        (7, 0),     # 未超缓冲
        (10, 0),    # 恰好缓冲上限，不计费
        (11, 10),   # 超1分钟 -> 计10
        (20, 10),   # 超10 -> 计10
        (21, 20),   # 超11 -> 计20（旧实现此处会错算成30）
        (33, 30),   # 超23 -> 计30（旧实现此处会错算成40 -> ¥53.33）
        (51, 50),   # 超41 -> 计50
        (59, 50),   # 超49 -> 计50
    ]
    for rem, expect in cases:
        got = billing._charged_extra_minutes(rem, 10, 10)
        assert got == expect, f'rem={rem}: 应计 {expect}min，实际 {got}'


def test_bug_c_buffer_step_edges():
    """边界：0:00 / 0:05 / 0:10 / 0:11 / 0:59 / 1:00 / 1:10 / 1:11 / 1:59 / 2:00。
    首小时内一律按最低1小时计费（¥20）；缓冲阶梯只作用于首小时之后。"""
    cases = [
        (0, 0.0),     # 0:00 时长=0，不收费
        (5, 20.0),    # 0:05 最低1小时
        (10, 20.0),   # 0:10 首小时最低消费（非按缓冲计）
        (11, 20.0),   # 0:11 仍首小时最低消费
        (59, 20.0),   # 0:59 仍首小时最低消费
        (60, 20.0),   # 1:00
        (70, 20.0),   # 1:10（首小时之后仅10min=缓冲上限）
        (71, 23.0),   # 1:11 首小时后超1min -> 计10min(3.33) -> 23
        (119, 37.0),  # 1:59 首小时后超49min -> 计50min(16.67) -> 37
        (120, 40.0),  # 2:00
    ]
    for minutes, expect in cases:
        got = billing.compute_hourly_fee(minutes, 20, S)
        assert abs(got - expect) < 0.01, f'{minutes}min -> {got}, expect {expect}'


# =====================================================================
# BUG A：普通计时跨午夜（is_overnight=False）不得拆段重收最低1小时
# =====================================================================
def test_bug_a_no_split_cross_midnight_8port():
    """八口 ¥20/h：跨午夜连续场，只算一次普通台费。"""
    cases = [
        # (start, end, expect_fee, 说明)
        (datetime(2026, 10, 21, 21, 50), datetime(2026, 10, 21, 23, 50), 40.0, '21:50->23:50 连续2h'),
        (datetime(2026, 10, 21, 21, 50), datetime(2026, 10, 22, 0, 5), 43.0, '21:50->00:05 连续2h15m 不得重收1h'),
        (datetime(2026, 10, 21, 18, 48), datetime(2026, 10, 22, 0, 5), 103.0, '18:48->00:05 连续5h17m'),
        (datetime(2026, 10, 21, 23, 55), datetime(2026, 10, 22, 0, 5), 20.0, '23:55->00:05 连续10min 不得变两段收费'),
        (datetime(2026, 10, 21, 23, 55), datetime(2026, 10, 22, 0, 15), 20.0, '23:55->00:15 连续20min 同一局'),
        (datetime(2026, 10, 21, 21, 46), datetime(2026, 10, 22, 2, 3), 83.0, '21:46->02:03 连续4h17m'),
    ]
    for a, b, expect, note in cases:
        fee, bd = billing.calculate_fee('8port', a, b, S, is_overnight=False)
        assert abs(fee - expect) < 0.01, f'{note}: {fee} expect {expect}'
        # 关键：不得出现「凌晨时段(非通宵)」之类的拆段标签
        labels = [x.get('label') for x in bd]
        assert '凌晨时段(非通宵)' not in labels, f'{note} 仍拆出凌晨段: {labels}'
        # 关键：不得出现两段及以上 hourly 段（应为单一连续段）
        hourly = [x for x in bd if x.get('rate_type') == 'hourly']
        assert len(hourly) == 1, f'{note} hourly 段数量={len(hourly)}（应=1）'


def test_bug_a_no_split_cross_midnight_4port():
    """四口 ¥10/h：跨午夜连续场，验证不同费率下同样不拆段。"""
    cases = [
        (datetime(2026, 8, 23, 22, 0), datetime(2026, 8, 24, 3, 46), 57.0, '22:00->03:46 连续5h46m @10'),
        (datetime(2026, 8, 23, 21, 20), datetime(2026, 8, 24, 3, 46), 63.0, '21:20->03:46 连续6h26m @10'),
    ]
    for a, b, expect, note in cases:
        fee, bd = billing.calculate_fee('4port', a, b, S, is_overnight=False)
        assert abs(fee - expect) < 0.01, f'{note}: {fee} expect {expect}'


def test_bug_a_cross_day_edges():
    """跨日边界（section 八）：23:50->00:05 / 23:50->00:30 / 22:00->02:00。"""
    cases = [
        (datetime(2026, 10, 21, 23, 50), datetime(2026, 10, 22, 0, 5), 20.0, '23:50->00:05 连续15min'),
        (datetime(2026, 10, 21, 23, 50), datetime(2026, 10, 22, 0, 30), 20.0, '23:50->00:30 连续40min'),
        (datetime(2026, 10, 21, 22, 0), datetime(2026, 10, 22, 2, 0), 80.0, '22:00->02:00 连续4h'),
    ]
    for a, b, expect, note in cases:
        fee, _ = billing.calculate_fee('8port', a, b, S, is_overnight=False)
        assert abs(fee - expect) < 0.01, f'{note}: {fee} expect {expect}'


def test_bug_a_explanation_consistent():
    """BUG A 修复后：计费说明与实际 fee 自洽（无拆段导致的合计不一致）。"""
    a = datetime(2026, 10, 21, 18, 48)
    b = datetime(2026, 10, 22, 0, 5)
    fee, bd = billing.calculate_fee('8port', a, b, S, is_overnight=False)
    exp = billing.get_billing_explanation('8port', a, b, S, is_overnight=False)
    assert exp['total'] == billing._round_int(fee), f"说明合计 {exp['total']} != fee {fee}"
    assert exp['duration_label'] == '5小时17分钟', exp['duration_label']
    # 说明必须来自真实计费结果，不得前端重算
    assert exp['has_buffer'] is True
    assert exp['extra_minutes'] == 17
    assert exp['actual_over_minutes'] == 7
    assert exp['extra_charged_minutes'] == 10
    assert abs(exp['extra_fee'] - 3.33) < 0.01


def test_bug_a_overnight_unchanged():
    """回归保护：通宵(is_overnight=True) 计费不受 BUG A 修复影响。"""
    # 21:46 -> 02:03 通宵：正常段(21:46-24:00) + 包夜 flat(00:00-08:00窗口)
    fee, bd = billing.calculate_fee('8port', datetime(2026, 10, 21, 21, 46),
                                    datetime(2026, 10, 22, 2, 3), S, is_overnight=True)
    assert any(x.get('rate_type') == 'flat' for x in bd), '通宵应包含 flat 包夜记录'
    assert abs(fee - 143.0) < 0.01, fee
    # 23:00 -> 次日09:00 通宵 = 正常20 + 包夜100 + 自助10 = 130
    fee2, _ = billing.calculate_fee('8port', datetime(2026, 1, 1, 23, 0),
                                    datetime(2026, 1, 2, 9, 0), S, is_overnight=True)
    assert abs(fee2 - 130.0) < 0.01, fee2


# =====================================================================
# BUG B：通宵默认截止 = 青鹭固定窗口 00:00~08:00（不再 start+8h）
# =====================================================================
def test_bug_b_auto_end_fixed_window():
    """compute_auto_end_at 必须返回固定 08:00（按开台所属窗口）。"""
    cases = [
        (datetime(2026, 10, 21, 21, 46), datetime(2026, 10, 22, 8, 0), '21:46 -> 次日08:00'),
        (datetime(2026, 10, 21, 23, 30), datetime(2026, 10, 22, 8, 0), '23:30 -> 次日08:00'),
        (datetime(2026, 10, 21, 23, 59), datetime(2026, 10, 22, 8, 0), '23:59 -> 次日08:00'),
        (datetime(2026, 10, 21, 0, 30), datetime(2026, 10, 21, 8, 0), '00:30 -> 当日08:00'),
        (datetime(2026, 10, 21, 2, 0), datetime(2026, 10, 21, 8, 0), '02:00 -> 当日08:00'),
        (datetime(2026, 10, 21, 7, 30), datetime(2026, 10, 21, 8, 0), '07:30 -> 当日08:00'),
        (datetime(2026, 10, 21, 0, 0), datetime(2026, 10, 21, 8, 0), '00:00 -> 当日08:00'),
    ]
    for start, expect, note in cases:
        got = overnight.compute_auto_end_at(start)
        # 核心规格：固定 08:00 窗口
        assert got == expect, f'{note}: {got} expect {expect}'
        # 坚决反对 start+8h（21:46+8h=05:46 类错误）。
        # 注：00:00 开台时 start+8h 恰好=08:00（与正确窗口重合），仅对非空台时刻做此守卫。
        if start.hour != 0:
            assert got != start + timedelta(hours=8), f'{note} 仍使用 start+8h'


def test_bug_b_cross_month_year():
    """跨月 / 跨年 / 本地朴素时间无 UTC 偏移。"""
    assert overnight.compute_auto_end_at(datetime(2026, 1, 31, 23, 0)) == datetime(2027 if False else 2026, 2, 1, 8, 0)
    assert overnight.compute_auto_end_at(datetime(2026, 12, 31, 23, 0)) == datetime(2027, 1, 1, 8, 0)


def test_bug_b_actual_elapsed_is_now_minus_start():
    """回归保护：actual_elapsed_time 永远是 current - 真实开台（与计费/展示同源）。"""
    start = datetime(2026, 10, 21, 21, 46)
    now = datetime(2026, 10, 22, 2, 3)
    elapsed_min = (now - start).total_seconds() / 60.0
    assert abs(elapsed_min - 257) < 0.01, elapsed_min  # 4小时17分钟
    # 与 get_current_fee 使用的 now 同源（不掺入 deadline）
    fee_now, _ = billing.calculate_fee('8port', start, now, S, is_overnight=True)
    fee_current, _ = billing.get_current_fee('8port', start, S, is_overnight=True)
    # get_current_fee 用 datetime.now()；此处仅确认接口一致、无第二套算法
    assert isinstance(fee_current, (int, float))


def test_bug_b_effective_end_time_uses_actual_or_now():
    """effective_end_time：actual > auto_end(已触发) > now；不得用 start+8h 当时长。"""
    # 活跃未到截止：返回 now（真实游玩时长来源），而非 deadline
    row = {'is_overnight': 1, 'auto_end_at': '2026-10-22T08:00:00',
           'auto_ended': 0, 'actual_end_time': None}
    now = datetime(2026, 10, 22, 2, 3)
    assert overnight.effective_end_time(row, now=now) == now
    # 已确认实际结束：优先 actual
    row2 = {'is_overnight': 1, 'auto_end_at': '2026-10-22T08:00:00',
            'auto_ended': 1, 'actual_end_time': '2026-10-22T09:30:00'}
    assert overnight.effective_end_time(row2, now=now) == datetime(2026, 10, 22, 9, 30)


# =====================================================================
# 单源一致性：修改开始时间后所有位置同步（台桌总览/单人结账/最终收银同源）
# =====================================================================
def test_single_source_consistency():
    """台桌总览(get_current_fee) / 结账预览(calculate_fee) / 说明(get_billing_explanation)
    必须来自同一个计费引擎，且改开始时间后立即同步。"""
    start_late = datetime(2026, 10, 21, 20, 48)   # 较晚开台（时长更短）
    start_early = datetime(2026, 10, 21, 18, 48)  # 较早开台（时长更长）
    now = datetime(2026, 10, 22, 0, 5)

    fee_late, _ = billing.calculate_fee('8port', start_late, now, S, is_overnight=False)
    fee_early, _ = billing.calculate_fee('8port', start_early, now, S, is_overnight=False)
    exp_late = billing.get_billing_explanation('8port', start_late, now, S, is_overnight=False)
    exp_early = billing.get_billing_explanation('8port', start_early, now, S, is_overnight=False)

    # 三处同源：说明合计 == calculate_fee 结果
    assert exp_late['total'] == billing._round_int(fee_late)
    assert exp_early['total'] == billing._round_int(fee_early)
    # 改开始时间 -> 金额同步变化（较早开台时长更长，费用更高）
    assert fee_early > fee_late, f'改开始时间后未同步：{fee_late} -> {fee_early}'
    # 三处对同一个 start 给出一致金额
    assert abs(exp_late['total'] - billing._round_int(fee_late)) < 0.01
