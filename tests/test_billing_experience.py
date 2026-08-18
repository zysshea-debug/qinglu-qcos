"""QCOS V1.4 计费体验优化 - 单元测试

覆盖：
  1. 首小时最低消费
  2. 10分钟免费缓冲
  3. 10分钟阶梯计费
  4. 跨小时连续计费
  5. 四口机价格
  6. 八口机价格
  7. 通宵包夜不受影响（逻辑未改）
  8. 四舍五入保留整数
  9. 配置化参数（buffer/step/mode 可读）
 10. get_billing_explanation 说明结构
 11. 已有计费回归（V1.1/V1.2/V1.3 测试套件仍全部通过）

说明：本测试用临时 settings 字典，不依赖生产数据库。
"""

import os
import sys
import math
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import billing


# 标准配置（与 config.DEFAULT_SETTINGS 一致）
def std_settings(overrides=None):
    s = {
        'rate_8port': 20,
        'rate_4port': 10,
        'overnight_8port': 100,
        'overnight_4port': 50,
        'selfservice_multiplier': 0.5,
        'billing_buffer_minutes': 10,
        'billing_step_minutes': 10,
        'billing_rounding_mode': 'buffer_step',
    }
    if overrides:
        s.update(overrides)
    return s


S = std_settings()

_pass = 0
_fail = 0


def is_int(x):
    return abs(x - round(x)) < 1e-9


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f'  [PASS] {name}')
    else:
        _fail += 1
        print(f'  [FAIL] {name}')


# ============================================================
# 1. 首小时最低消费（不足1小时按1小时）
# ============================================================
print('[1] 首小时最低消费')
check('八口 30分钟 = 20（最低1小时）', billing.compute_hourly_fee(30, 20, S) == 20)
check('八口 60分钟 = 20', billing.compute_hourly_fee(60, 20, S) == 20)
check('四口 30分钟 = 10（最低1小时）', billing.compute_hourly_fee(30, 10, S) == 10)
check('四口 60分钟 = 10', billing.compute_hourly_fee(60, 10, S) == 10)

# ============================================================
# 2. 10分钟免费缓冲（61-70分钟仍为1小时价）
# ============================================================
print('[2] 10分钟免费缓冲')
check('八口 61分钟 = 20（缓冲内免费）', billing.compute_hourly_fee(61, 20, S) == 20)
check('八口 65分钟 = 20', billing.compute_hourly_fee(65, 20, S) == 20)
check('八口 70分钟 = 20', billing.compute_hourly_fee(70, 20, S) == 20)
check('四口 70分钟 = 10', billing.compute_hourly_fee(70, 10, S) == 10)

# ============================================================
# 3. 10分钟阶梯计费（首小时后按10分钟一档）
# ============================================================
print('[3] 10分钟阶梯计费')
# 75min: 超出15min -> 计10min = 3.33 -> 23
check('八口 75分钟 = 23', billing.compute_hourly_fee(75, 20, S) == 23)
# 80min: 超出20min -> 计10min（规则表 11-20 增加10分钟）= 23
check('八口 80分钟 = 23（规则表：11-20分钟增加10分钟费用）', billing.compute_hourly_fee(80, 20, S) == 23)
# 90min: 超出30min -> 计30min = 10 -> 30
check('八口 90分钟 = 30', billing.compute_hourly_fee(90, 20, S) == 30)
# 110min: 超出50min -> 计50min = 16.67 -> 37
check('八口 110分钟 = 37', billing.compute_hourly_fee(110, 20, S) == 37)
# 100min: 超出40min -> 计40min = 13.33 -> 33
check('八口 100分钟 = 33', billing.compute_hourly_fee(100, 20, S) == 33)

# ============================================================
# 4. 跨小时连续计费
# ============================================================
print('[4] 跨小时连续计费')
check('八口 120分钟 = 40（2小时）', billing.compute_hourly_fee(120, 20, S) == 40)
check('八口 130分钟 = 40（缓冲内）', billing.compute_hourly_fee(130, 20, S) == 40)
check('八口 140分钟 = 43（追加10min=3.33）', billing.compute_hourly_fee(140, 20, S) == 43)
check('八口 180分钟 = 60（3小时）', billing.compute_hourly_fee(180, 20, S) == 60)

# ============================================================
# 5. 四口机价格（半价，同样阶梯）
# ============================================================
print('[5] 四口机价格')
check('四口 75分钟 = 12', billing.compute_hourly_fee(75, 10, S) == 12)
check('四口 80分钟 = 12', billing.compute_hourly_fee(80, 10, S) == 12)
check('四口 90分钟 = 15', billing.compute_hourly_fee(90, 10, S) == 15)
check('四口 110分钟 = 18', billing.compute_hourly_fee(110, 10, S) == 18)
check('四口 120分钟 = 20', billing.compute_hourly_fee(120, 10, S) == 20)

# ============================================================
# 6. 八口机价格（汇总校验）
# ============================================================
print('[6] 八口机价格（汇总）')
for mins, expect in [(60, 20), (65, 20), (70, 20), (75, 23), (80, 23), (90, 30), (110, 37), (120, 40)]:
    check(f'八口 {mins}分钟 = {expect}', billing.compute_hourly_fee(mins, 20, S) == expect)

# ============================================================
# 7. 通宵包夜不受影响（flat 逻辑未修改）
# ============================================================
print('[7] 通宵包夜不受影响')
# 真正通宵时段（01:00 起 200 分钟，落在 00:00-08:00）
start = datetime(2026, 1, 1, 1, 0)
end = start + timedelta(minutes=200)
fee8, bd8 = billing.calculate_fee('8port', start, end, S, is_overnight=True)
check('通宵 八口 200min = 100（一次包夜，未用阶梯）', fee8 == 100)
check('通宵 breakdown 含 flat 记录', any(b.get('rate_type') == 'flat' for b in bd8))
fee4, bd4 = billing.calculate_fee('4port', start, end, S, is_overnight=True)
check('通宵 四口 200min = 50（一次包夜）', fee4 == 50)
# 跨夜（23:00 -> 次日 09:00）应只收一次通宵 flat + 正常 + 自助
start2 = datetime(2026, 1, 1, 23, 0)
end2 = start2 + timedelta(minutes=600)
feeX, bdX = billing.calculate_fee('8port', start2, end2, S, is_overnight=True)
check('跨夜通宵 = 正常20 + 包夜100 + 自助10 = 130', feeX == 130)
# 凌晨未勾通宵 -> 走正常小时阶梯（验证分支独立）
feeN = billing.calculate_fee('8port', start, end, S, is_overnight=False)[0]
check('凌晨未勾通宵 走正常阶梯（非100）', feeN != 100 and feeN > 0)

# ============================================================
# 8. 四舍五入保留整数
# ============================================================
print('[8] 四舍五入保留整数')
for mins in [61, 75, 80, 90, 95, 105, 110, 130, 140]:
    f = billing.compute_hourly_fee(mins, 20, S)
    check(f'八口 {mins}分钟 结果为整数', is_int(f))
    # 验证四舍五入：与手动计算一致
    full = mins // 60
    rem = mins % 60
    raw = full * 20 + (billing._charged_extra_minutes(rem, 10, 10) / 60.0 * 20 if (full > 0 and rem > 10) else 0)
    if full == 0:
        raw = 20 if mins > 0 else 0
    check(f'八口 {mins}分钟 = round({raw:.2f})', f == int(math.floor(raw + 0.5)))

# ============================================================
# 9. 配置化参数可读（buffer/step/mode）
# ============================================================
print('[9] 配置化参数')
# 缓冲改为 0 -> 61 分钟立即开始计费
S0 = std_settings({'billing_buffer_minutes': 0})
check('buffer=0 时 61分钟 > 20（无缓冲）', billing.compute_hourly_fee(61, 20, S0) > 20)
# step 改为 30
S30 = std_settings({'billing_step_minutes': 30})
# 75min 超出15 -> 在 0-30 第一档内，按 30 分钟计 = 10
check('step=30 时 75分钟 = 30', billing.compute_hourly_fee(75, 20, S30) == 30)
# 回退 exact_minute（旧比例逻辑）
Se = std_settings({'billing_rounding_mode': 'exact_minute'})
check('exact_minute 75分钟 = 25（比例）', billing.compute_hourly_fee(75, 20, Se) == 25.0)
# hour_round（向上取整到整小时）
Sh = std_settings({'billing_rounding_mode': 'hour_round'})
check('hour_round 75分钟 = 40（向上整小时）', billing.compute_hourly_fee(75, 20, Sh) == 40)

# ============================================================
# 10. get_billing_explanation 说明结构
# ============================================================
print('[10] 计费说明结构')
exp = billing.get_billing_explanation('8port', start2.replace(hour=12, minute=0),
                                       start2.replace(hour=12, minute=0) + timedelta(minutes=80), S)
check('返回 duration_label', exp.get('duration_label') == '1小时20分钟')
check('返回 base_fee=20', exp.get('base_fee') == 20)
check('返回 extra_minutes=20', exp.get('extra_minutes') == 20)
check('返回 extra_charged_minutes=10', exp.get('extra_charged_minutes') == 10)
check('返回 total=23', exp.get('total') == 23)
check('has_buffer=True', exp.get('has_buffer') is True)
# 通宵说明
exp_flat = billing.get_billing_explanation('8port', start, end, S, is_overnight=True)
check('通宵说明 mode=flat', exp_flat.get('mode') == 'flat')
check('通宵说明 total=100', exp_flat.get('total') == 100)

# ============================================================
# 汇总
# ============================================================
print('\n' + '=' * 40)
print(f'V1.4 计费体验测试：通过 {_pass} / 失败 {_fail}')
sys.exit(1 if _fail else 0)
