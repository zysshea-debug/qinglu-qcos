"""青鹭台费缓冲阶梯回归（台费计费 Bug 修复）

聚焦 2026-10 台费计费 Bug：整小时后的零碎分钟，必须先扣 10 分钟免费缓冲，
剩余部分再按 10 分钟阶梯向上取整。早期实现在余数 > 20 分钟时多算一个阶梯
（等价于把已扣除的免费缓冲重新计入收费）。

覆盖：
  - 用户给出的标准价 20 元/小时 用例表（2:00 ~ 4:07）
  - 0 小时 / 1 小时 / 跨多小时
  - 修改开始时间后重新计算（同一计费源 calculate_fee）
  - get_billing_explanation 透出的实际超出 / 计费时长字段
  - 金额精度沿用项目既有规则（compute_hourly_fee 末尾整数四舍五入，不改）

不依赖生产数据库，使用临时 settings。
"""

import os
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import billing

# 标准价 八口 20 元/小时；与 config.DEFAULT_SETTINGS 一致
_SETTINGS = {
    'rate_8port': 20, 'rate_4port': 10,
    'overnight_8port': 100, 'overnight_4port': 50,
    'selfservice_multiplier': 0.5,
    'billing_buffer_minutes': 10, 'billing_step_minutes': 10,
    'billing_rounding_mode': 'buffer_step',
}
S = _SETTINGS

_pass = 0
_fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f'  [PASS] {name}')
    else:
        _fail += 1
        print(f'  [FAIL] {name}')


def precise_fee(start, minutes):
    """通过公开 API 取精确（未取整）台费 = base_fee + extra_fee 以及说明字典。"""
    end = start + timedelta(minutes=minutes)
    fee, _ = billing.calculate_fee('8port', start, end, S)
    exp = billing.get_billing_explanation('8port', start, end, S)
    return round(exp['base_fee'] + exp['extra_fee'], 2), exp


# 固定在正常时段，避免跨时段拆分干扰（本用例均 < 5 小时）
_BASE = datetime(2026, 1, 1, 14, 0, 0)


print('[A] 标准价 20 元/小时 用户用例表')
CASES = [
    ('2:00', 120, 40.00, 40),
    ('2:07', 127, 40.00, 40),
    ('2:10', 130, 40.00, 40),
    ('2:11', 131, 43.33, 43),
    ('2:20', 140, 43.33, 43),
    ('2:21', 141, 46.67, 47),
    ('2:30', 150, 46.67, 47),
    ('2:31', 151, 50.00, 50),
    ('2:33', 153, 50.00, 50),
    ('4:07', 247, 80.00, 80),
]
for label, mins, exp_precise, exp_total in CASES:
    precise, exp = precise_fee(_BASE, mins)
    check(f'{label} 精确台费={exp_precise}', precise == exp_precise)
    check(f'{label} 最终收费(取整)={exp_total}', exp['total'] == exp_total)


print('[B] 边界：0 小时 / 1 小时 / 跨多小时')
check('0 分钟 = 0', precise_fee(_BASE, 0)[0] == 0.0)
check('1 小时(60分钟)=20.00', precise_fee(_BASE, 60)[0] == 20.00)
check('3 小时(180分钟)=60.00', precise_fee(_BASE, 180)[0] == 60.00)
check('5 小时(300分钟)=100.00', precise_fee(_BASE, 300)[0] == 100.00)
# 跨整小时缓冲边界：3:10 仍在缓冲内 = 60
check('3:10(190分钟)=60.00（缓冲内）', precise_fee(_BASE, 190)[0] == 60.00)


print('[C] 缓冲阶梯单调正确（先扣10再阶梯，不把缓冲重新计入）')
# 关键：21~30 分钟余量应计 20（旧 Bug 计 30）
check('_charged_extra_minutes(21,10,10)=20', billing._charged_extra_minutes(21, 10, 10) == 20)
check('_charged_extra_minutes(30,10,10)=20', billing._charged_extra_minutes(30, 10, 10) == 20)
check('_charged_extra_minutes(31,10,10)=30', billing._charged_extra_minutes(31, 10, 10) == 30)
check('_charged_extra_minutes(33,10,10)=30', billing._charged_extra_minutes(33, 10, 10) == 30)
check('_charged_extra_minutes(40,10,10)=30', billing._charged_extra_minutes(40, 10, 10) == 30)
check('_charged_extra_minutes(50,10,10)=40', billing._charged_extra_minutes(50, 10, 10) == 40)
check('_charged_extra_minutes(59,10,10)=50', billing._charged_extra_minutes(59, 10, 10) == 50)


print('[D] 修改开始时间后重新计算（同一计费源 calculate_fee）')
# 原开台 14:00，游玩 2:33 -> 结束 16:33；精确 50.00
fee_a, exp_a = precise_fee(_BASE, 153)
check('原 2:33 = 50.00', fee_a == 50.00)
# 修改开始时间：开台提前 10 分钟（13:50 开台），仍 16:33 结束 -> 2:43
# 2:43: full=2, rem=43 -> 先扣10剩33 -> 计40min -> 额外 40/60*20=13.33 -> 53.33
fee_b, exp_b = precise_fee(_BASE - timedelta(minutes=10), 163)
check('提前10分钟(2:43) 精确=53.33', fee_b == 53.33)
check('提前10分钟(2:43) 实际超出=33', exp_b['actual_over_minutes'] == 33)
check('提前10分钟(2:43) 计费时长=40', exp_b['extra_charged_minutes'] == 40)
# 修改开始时间：开台推后（14:30 开台），16:33 结束 -> 2:03
# 2:03: full=2, rem=3 <= 缓冲 -> 40.00
fee_c, exp_c = precise_fee(_BASE + timedelta(minutes=30), 123)
check('推后30分钟(2:03) = 40.00（缓冲内）', fee_c == 40.00)


print('[E] get_billing_explanation 字段完整性（2:33 示例）')
_, exp = precise_fee(_BASE, 153)
check('duration_label=2小时33分钟', exp['duration_label'] == '2小时33分钟')
check('full_hours=2', exp['full_hours'] == 2)
check('base_fee=40.00', exp['base_fee'] == 40.00)
check('extra_minutes=33', exp['extra_minutes'] == 33)
check('buffer_minutes=10', exp['buffer_minutes'] == 10)
check('actual_over_minutes=23', exp['actual_over_minutes'] == 23)
check('extra_charged_minutes=30', exp['extra_charged_minutes'] == 30)
check('extra_fee=10.00', exp['extra_fee'] == 10.00)
check('has_buffer=True', exp['has_buffer'] is True)


print('\n' + '=' * 40)
print(f'台费缓冲阶梯回归：通过 {_pass} / 失败 {_fail}')

if __name__ == '__main__':
    sys.exit(1 if _fail else 0)


def test_table_fee_buffer_step_suite():
    """pytest 入口：套件在导入期已确定性执行完，此处断言其结果为全通过。"""
    assert _pass > 0, '台费缓冲阶梯回归套件未实际执行'
    assert _fail == 0, f'台费缓冲阶梯回归失败 {_fail} 项'
