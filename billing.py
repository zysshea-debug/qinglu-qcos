"""青鹭收银系统 - 计费引擎

时段划分：
  12:00-24:00  正常时段  按小时计费
  00:00-08:00  通宵时段  包夜 flat rate（每个通宵时段收一次）
  08:00-12:00  自助时段  按小时计费（半价）
"""

import math
from datetime import datetime, timedelta


def get_period(hour):
    """根据小时判断时段"""
    if 0 <= hour < 8:
        return 'overnight'
    elif 8 <= hour < 12:
        return 'selfservice'
    else:
        return 'normal'


def split_session(start, end):
    """按时段切分会话，返回分段列表"""
    segments = []
    current = start

    while current < end:
        period = get_period(current.hour)

        if period == 'overnight':
            period_end = current.replace(hour=8, minute=0, second=0, microsecond=0)
        elif period == 'selfservice':
            period_end = current.replace(hour=12, minute=0, second=0, microsecond=0)
        else:
            if current.hour >= 12:
                period_end = (current + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                period_end = current.replace(hour=0, minute=0, second=0, microsecond=0)

        seg_end = min(period_end, end)
        minutes = (seg_end - current).total_seconds() / 60

        segments.append({
            'period': period,
            'start': current.isoformat(),
            'end': seg_end.isoformat(),
            'minutes': round(minutes, 1)
        })
        current = seg_end

    return segments


def _round_int(x):
    """四舍五入保留整数（正数安全）"""
    return int(math.floor(x + 0.5))


def _parse_dt(x):
    if isinstance(x, datetime):
        return x
    if isinstance(x, str):
        return datetime.fromisoformat(x)
    return datetime.now()


def format_duration_backend(minutes):
    """把分钟数转成 '1小时20分钟' 这种中文文案"""
    minutes = int(round(minutes))
    h = minutes // 60
    m = minutes % 60
    if h and m:
        return f'{h}小时{m}分钟'
    if h:
        return f'{h}小时'
    return f'{m}分钟'


def _charged_extra_minutes(rem, buffer, step):
    """超出整小时的零碎分钟(rem)应计费的分钟数（缓冲阶梯规则表）。

    仅作用于「首小时之后的零碎时间」；整小时已按 full*rate 计。
    规则（buffer=10, step=10）：
      0-10 免费；11-20 计10；21-30 计30；31-40 计40；41-50 计50；51-60 计60。
    该表为规范文本（第三节），与示例 60/65/70/75/90/110 一致。
    """
    if rem <= buffer:
        return 0
    over = rem - buffer
    k = (over + step - 1) // step  # 第几档（从1开始）
    if k <= 0:
        return 0
    if k == 1:
        return step
    # 第2档起：step, 3*step, 4*step ...（与规则表一致）
    return (k + 1) * step


def compute_hourly_fee(minutes, rate, settings=None):
    """正常小时计费：首小时最低消费 + 缓冲阶梯。

    仅用于非通宵（小时费率）场景，例如 正常时段 / 自助时段(半价) / 凌晨未勾通宵。
    通宵包夜(flat) 不调用本函数。

    billing_rounding_mode:
      - 'buffer_step'（默认）：首小时最低消费（不足1小时按1小时），
        之后每小时末享 buffer 分钟免费缓冲，超出按 step 分钟阶梯计费，
        最终金额四舍五入保留整数。
      - 'hour_round'：按整小时向上取整。
      - 'exact_minute'：按精确分钟比例计费（旧逻辑，便于回退）。
    """
    if settings is None:
        settings = {}
    mode = settings.get('billing_rounding_mode', 'buffer_step')
    buffer = int(settings.get('billing_buffer_minutes', 10))
    step = int(settings.get('billing_step_minutes', 10))
    minutes = max(0, int(round(minutes)))

    if mode == 'exact_minute':
        return round(rate * minutes / 60.0, 2)
    if mode == 'hour_round':
        return float(_round_int(rate * math.ceil(minutes / 60.0)))

    # buffer_step（默认）
    if minutes <= 0:
        return 0.0
    full = minutes // 60
    rem = minutes % 60
    if full == 0:
        fee = rate  # 最低一小时消费
    else:
        fee = full * rate
        if rem > buffer:
            fee += _charged_extra_minutes(rem, buffer, step) / 60.0 * rate
    return float(_round_int(round(fee, 2)))


def get_billing_explanation(machine_type, start, end, settings, is_overnight=True):
    """生成面向客人的计费说明：游玩时长 / 基础 / 超出 / 追加 / 合计。"""
    if settings is None:
        settings = {}
    start = _parse_dt(start)
    end = _parse_dt(end)
    total_minutes = max(0.0, (end - start).total_seconds() / 60.0)
    fee, breakdown = calculate_fee(machine_type, start, end, settings, is_overnight=is_overnight)
    base_rate = settings.get(f'rate_{machine_type}', 20)
    buffer = int(settings.get('billing_buffer_minutes', 10))
    step = int(settings.get('billing_step_minutes', 10))
    mode = settings.get('billing_rounding_mode', 'buffer_step')

    if any(b.get('rate_type') == 'flat' for b in breakdown):
        return {
            'mode': 'flat', 'is_overnight': True,
            'duration_minutes': round(total_minutes, 1),
            'duration_label': format_duration_backend(total_minutes),
            'base_fee': round(fee, 2), 'base_label': '通宵包夜(固定价)',
            'extra_minutes': 0, 'extra_charged_minutes': 0, 'extra_fee': 0.0,
            'subtotal': round(fee, 2), 'total': round(fee, 2),
            'buffer_minutes': 0, 'step_minutes': 0, 'has_buffer': False,
            'note': '通宵包夜按固定价计费，不适用缓冲阶梯。',
        }

    hourly_segs = [b for b in breakdown if b.get('rate_type') == 'hourly']
    if not hourly_segs:
        return {
            'mode': mode, 'is_overnight': False,
            'duration_minutes': round(total_minutes, 1),
            'duration_label': format_duration_backend(total_minutes),
            'base_fee': 0.0, 'base_label': '小时费',
            'extra_minutes': 0, 'extra_charged_minutes': 0, 'extra_fee': 0.0,
            'subtotal': 0.0, 'total': 0,
            'buffer_minutes': buffer, 'step_minutes': step, 'has_buffer': True,
            'note': '',
        }

    rate = hourly_segs[0]['rate']
    hourly_minutes = sum(b['minutes'] for b in hourly_segs)
    full = int(round(hourly_minutes)) // 60
    rem = int(round(hourly_minutes)) % 60
    if full == 0:
        base_fee = rate
        extra_charged = 0
        extra_fee = 0.0
    else:
        base_fee = full * rate
        if rem > buffer:
            extra_charged = _charged_extra_minutes(rem, buffer, step)
            extra_fee = extra_charged / 60.0 * rate
        else:
            extra_charged = 0
            extra_fee = 0.0
    subtotal = base_fee + extra_fee
    return {
        'mode': mode, 'is_overnight': False,
        'duration_minutes': round(total_minutes, 1),
        'duration_label': format_duration_backend(total_minutes),
        'base_fee': round(base_fee, 2),
        'base_label': '首小时(最低消费)' if full == 0 else '小时费',
        'extra_minutes': int(rem),
        'extra_charged_minutes': int(extra_charged),
        'extra_fee': round(extra_fee, 2),
        'subtotal': round(subtotal, 2),
        'total': _round_int(fee),
        'buffer_minutes': buffer, 'step_minutes': step, 'has_buffer': True,
        'note': f"每小时结束享 {buffer} 分钟免费缓冲，超出按 {step} 分钟阶梯计费（标准价 ¥{rate}/h）。",
    }


def calculate_fee(machine_type, start, end, settings, is_overnight=True):
    """计算台费，返回 (total_fee, breakdown)

    Args:
        is_overnight: 是否应用通宵包夜费率；False 时 00:00-08:00 按正常小时费率计费
    """
    segments = split_session(start, end)

    breakdown = []
    total_fee = 0
    overnight_dates = set()

    base_rate = settings.get(f'rate_{machine_type}', 20)
    overnight_rate = settings.get(f'overnight_{machine_type}', 100)
    ss_multiplier = settings.get('selfservice_multiplier', 0.5)

    for seg in segments:
        period = seg['period']
        minutes = seg['minutes']

        if period == 'overnight':
            if is_overnight:
                seg_date = datetime.fromisoformat(seg['start']).date()
                if seg_date not in overnight_dates:
                    overnight_dates.add(seg_date)
                    total_fee += overnight_rate
                    breakdown.append({
                        'period': 'overnight',
                        'label': '通宵包夜',
                        'minutes': minutes,
                        'rate': overnight_rate,
                        'rate_type': 'flat',
                        'amount': round(overnight_rate, 2)
                    })
            else:
                # 未勾选通宵：按正常时段小时费率计费（V1.4 缓冲阶梯）
                amount = compute_hourly_fee(minutes, base_rate, settings)
                total_fee += amount
                breakdown.append({
                    'period': 'normal',
                    'label': '凌晨时段(非通宵)',
                    'minutes': minutes,
                    'rate': base_rate,
                    'rate_type': 'hourly',
                    'amount': round(amount, 2)
                })
        elif period == 'normal':
            amount = compute_hourly_fee(minutes, base_rate, settings)
            total_fee += amount
            breakdown.append({
                'period': 'normal',
                'label': '正常时段',
                'minutes': minutes,
                'rate': base_rate,
                'rate_type': 'hourly',
                'amount': round(amount, 2)
            })
        elif period == 'selfservice':
            rate = base_rate * ss_multiplier
            amount = compute_hourly_fee(minutes, rate, settings)
            total_fee += amount
            breakdown.append({
                'period': 'selfservice',
                'label': '自助时段(半价)',
                'minutes': minutes,
                'rate': round(rate, 2),
                'rate_type': 'hourly',
                'amount': round(amount, 2)
            })

    # 取整
    rounding = settings.get('rounding', 'none')
    if rounding == 'up_half_hour':
        total_fee = math.ceil(total_fee * 2) / 2
    elif rounding == 'up_hour':
        total_fee = math.ceil(total_fee)

    return round(total_fee, 2), breakdown


def get_current_fee(machine_type, start_time, settings, is_overnight=True):
    """实时台费（用于展示）"""
    return calculate_fee(machine_type, start_time, datetime.now(), settings, is_overnight=is_overnight)


def calculate_discount(fee, discount_type, max_deduction):
    """计算抵扣金额"""
    if discount_type == 'free':
        return round(min(fee, max_deduction), 2)
    elif discount_type == 'half':
        return round(min(fee * 0.5, max_deduction), 2)
    elif discount_type == 'discount':
        return round(min(fee * 0.2, max_deduction), 2)
    return 0


def calculate_manual_discount(fee, discount_type, value):
    """计算手动台费折扣金额

    Args:
        fee: 原始台费
        discount_type: 'amount' 金额减免 / 'percent' 折扣比例
        value: 减免金额 或 折扣百分比（如 20 表示 20% off / 八折）
    Returns:
        折扣金额（非负，不超过原始台费）
    """
    fee = float(fee or 0)
    value = float(value or 0)
    if value <= 0 or fee <= 0:
        return 0.0
    if discount_type == 'amount':
        return round(min(value, fee), 2)
    elif discount_type == 'percent':
        # value 为百分比，如 20 表示减免 20%
        return round(min(fee * value / 100.0, fee), 2)
    return 0.0
