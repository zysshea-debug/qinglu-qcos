"""青鹭收银系统 - 通宵桌动态截止 + 灵活实际结束时间 (V3)

三层时间模型：
  - start_time:        真实开台时间
  - auto_end_at:       系统默认保护截止（start + 8h，封顶 11:00）
  - actual_end_time:   人工确认真实结束时间

时间优先级（所有 preview / checkout / payment 统一使用 effective_end_time）：
  actual_end_time  >  auto_end_at(已触发自动截止)  >  now

设计要点：
  - 11:00 只是「系统默认自动截止线」，不是禁止玩家继续玩的硬上限；
    人工明确延长 / 事后确认实际结束时间可晚于 11:00（代表真实经营决定）。
  - auto_end_at 到达只停止默认时间增长，绝不自动付款 / 自动结账。
  - 无人值班后第二天，员工可事后把 actual_end_time 改为真实离开时间，重算账单。
  - 全部数据库操作都作用在传入的 db 连接上（不自行打开连接），便于测试注入临时库。
"""

from datetime import datetime, timedelta

AUTO_END_CAP_HOUR = 11            # 系统默认自动截止封顶小时（11:00）
OVERNIGHT_DEFAULT_HOURS = 8       # 通宵默认时长（小时）

# 延长快捷选项（分钟）：+30分钟 / +1小时 / +2小时
EXTEND_OPTIONS_MINUTES = [30, 60, 120]

# 结束时间修改审计原因
END_TIME_AUDIT_REASONS = [
    'OVERNIGHT_LATE_PLAY',   # 通宵客人玩到更晚
    'CUSTOMER_LEFT_EARLY',   # 客人提前离开
    'STAFF_LATE_CHECKOUT',   # 员工晚点结账
    'MANUAL_CORRECTION',     # 人工更正
    'OTHER',                 # 其他
]
VALID_AUDIT_REASONS = set(END_TIME_AUDIT_REASONS)

# 自动截止原因
AUTO_END_REASON_DEADLINE = 'AUTO_DEADLINE_REACHED'


def _parse_dt(x):
    """解析 datetime / ISO 字符串 -> datetime；不可解析返回 None。"""
    if isinstance(x, datetime):
        return x
    if isinstance(x, str) and x.strip():
        try:
            return datetime.fromisoformat(x)
        except ValueError:
            return None
    return None


def compute_auto_end_at(start_time, now=None):
    """计算系统默认保护截止。

    仅对通宵桌有意义（调用方先判断 is_overnight）。
    规则：start + 8h，封顶到「start+8h 所在自然日的 11:00」。

    验证：
      23:00 -> 07:00(次日)   23:30 -> 07:30   00:00 -> 08:00
      00:30 -> 08:30         01:00 -> 09:00   02:00 -> 10:00
      02:30 -> 10:30         03:00 -> 11:00   03:30 -> 11:00   04:00 -> 11:00
    """
    start = _parse_dt(start_time)
    if start is None:
        return None
    default = start + timedelta(hours=OVERNIGHT_DEFAULT_HOURS)
    cap_day = default.date()
    cap = start.replace(
        year=cap_day.year, month=cap_day.month, day=cap_day.day,
        hour=AUTO_END_CAP_HOUR, minute=0, second=0, microsecond=0,
    )
    return min(default, cap)


def is_auto_end_overdue(session_row, now=None):
    """overnight + 活跃 + 已越过 auto_end_at 且尚未人工结束？"""
    now = now or datetime.now()
    if not session_row.get('is_overnight'):
        return False
    if session_row.get('status') not in (None, 'active'):
        return False
    if session_row.get('auto_ended'):
        return False
    auto = _parse_dt(session_row.get('auto_end_at'))
    if auto is None:
        return False
    return now >= auto


def effective_end_time(session_row, now=None):
    """统一结账结束时间（所有金额计算唯一来源）。

    优先级：
      actual_end_time                       -> 人工确认真实结束（可晚于 11:00）
      auto_end_at（auto_ended 或已超时）     -> 系统默认保护截止
      now                                    -> 仍在正常进行中
    """
    now = now or datetime.now()
    actual = _parse_dt(session_row.get('actual_end_time'))
    if actual is not None:
        return actual
    auto = _parse_dt(session_row.get('auto_end_at'))
    if session_row.get('auto_ended') and auto is not None:
        return auto
    # 离线 / 超时恢复：overnight 且已越过 auto_end_at（尚未被 reaper 持久化也生效）
    if session_row.get('is_overnight') and auto is not None and now >= auto:
        return auto
    return now


def extend_auto_end_at(auto_end_at, minutes):
    """人工延长 auto_end_at（突破 11:00 封顶）。

    minutes 为相对增量（正数）；返回新的 datetime。
    人工延长代表真实经营决定，不受默认 11:00 封顶约束。
    """
    auto = _parse_dt(auto_end_at)
    if auto is None:
        return None
    if minutes is None or minutes <= 0:
        return auto
    return auto + timedelta(minutes=minutes)


def session_overnight_status(session_row, now=None):
    """返回 (label_key, label_text, deadline) 供前端展示。"""
    now = now or datetime.now()
    auto = _parse_dt(session_row.get('auto_end_at'))
    actual = _parse_dt(session_row.get('actual_end_time'))
    if actual is not None and session_row.get('end_time_confirmed'):
        return 'ended_confirmed', '已确认结束', actual
    if session_row.get('auto_ended'):
        return 'pending_confirm', '待确认结束时间', auto
    if session_row.get('is_overnight') and auto is not None and now < auto:
        return 'overnight_active', '通宵进行中', auto
    return 'active', '使用中', auto


# ====================== DB 层（作用在传入连接上）======================

def reap_overnight_sessions(db, now=None):
    """将已越过 auto_end_at 的活跃通宵桌标记为 auto_ended=1（幂等）。

    离线补偿（requirement 18）：QCOS 重启后发现通宵桌已超时，
    恢复 auto_ended=1、auto_end_at 不变（=默认截止）、end_time_confirmed=0，
    绝不写成 now（避免把「13:00 重启」误判为结束时间）。
    """
    now = now or datetime.now()
    rows = db.execute(
        """SELECT id, auto_end_at FROM sessions
           WHERE status='active' AND is_overnight=1 AND auto_ended=0
             AND auto_end_at IS NOT NULL"""
    ).fetchall()
    count = 0
    for r in rows:
        auto = _parse_dt(r['auto_end_at'])
        if auto is not None and now >= auto:
            db.execute(
                "UPDATE sessions SET auto_ended=1, auto_end_reason=? WHERE id=?",
                [AUTO_END_REASON_DEADLINE, r['id']]
            )
            count += 1
    return count


def ensure_auto_end_at(db, session_id, is_overnight, start_time):
    """创建 / 更新桌局时：若通宵且尚未设置，计算并写入 auto_end_at。

    - 非通宵桌：标记 is_overnight=0，清空自动截止（普通桌不受 overnight 逻辑影响）。
    - 通宵桌且 auto_end_at 为空：写入默认截止（start+8h 封顶 11:00）。
    幂等：已设置过的不会覆盖（避免 reaper 后回退 deadline）。
    """
    if not is_overnight:
        db.execute(
            "UPDATE sessions SET is_overnight=0, auto_end_at=NULL, auto_ended=0, "
            "auto_end_reason=NULL WHERE id=?", [session_id]
        )
        return None
    auto = compute_auto_end_at(start_time)
    if auto is None:
        return None
    db.execute(
        "UPDATE sessions SET is_overnight=1, auto_end_at=? WHERE id=? AND auto_end_at IS NULL",
        [auto.isoformat(timespec='seconds'), session_id]
    )
    # 若之前被标成非通宵，恢复 is_overnight
    db.execute(
        "UPDATE sessions SET is_overnight=1 WHERE id=? AND is_overnight=0", [session_id]
    )
    return auto


def write_end_time_audit(db, session_id, field, old_value, new_value, operator, reason, now=None):
    """记录一次结束时间修改（不可无痕改收费数据）。"""
    if reason not in VALID_AUDIT_REASONS:
        reason = 'OTHER'
    now = now or datetime.now()
    db.execute(
        """INSERT INTO end_time_audit
           (session_id, field, old_end_time, new_end_time, operator, reason, changed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [session_id, field,
         old_value.isoformat(timespec='seconds') if isinstance(old_value, datetime) else (old_value or ''),
         new_value.isoformat(timespec='seconds') if isinstance(new_value, datetime) else (new_value or ''),
         operator or 'unknown', reason, now.isoformat(timespec='seconds')]
    )


def set_actual_end_time(db, session_id, actual_end_time, operator, reason='MANUAL_CORRECTION', now=None):
    """人工确认真实结束时间（可晚于 auto_end_at / 11:00，可早于 auto_end_at）。

    写入 actual_end_time 与 end_time_confirmed=1，并记录审计。
    返回被修改的 session 行（dict）。
    """
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return None
    sess = dict(sess)
    old_value = sess.get('actual_end_time')
    new_dt = _parse_dt(actual_end_time)
    now = now or datetime.now()
    db.execute(
        """UPDATE sessions SET actual_end_time=?, end_time_confirmed=1,
           end_time_confirmed_at=?, end_time_confirmed_by=?
           WHERE id=?""",
        [new_dt.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'),
         operator, session_id]
    )
    write_end_time_audit(db, session_id, 'actual_end_time', old_value,
                         new_dt, operator, reason, now)
    return db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()


def confirm_default_end_time(db, session_id, operator, now=None):
    """结账确认时，若无人确认实际结束时间，默认把 auto_end_at 作为实际结束。

    记录审计原因 STAFF_LATE_CHECKOUT（员工晚点结账 / 确认默认截止）。
    """
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return None
    sess = dict(sess)
    auto = _parse_dt(sess.get('auto_end_at'))
    if auto is None:
        return sess
    now = now or datetime.now()
    old_value = sess.get('actual_end_time')
    db.execute(
        """UPDATE sessions SET actual_end_time=?, end_time_confirmed=1,
           end_time_confirmed_at=?, end_time_confirmed_by=?
           WHERE id=?""",
        [auto.isoformat(timespec='seconds'), now.isoformat(timespec='seconds'),
         operator, session_id]
    )
    write_end_time_audit(db, session_id, 'actual_end_time', old_value,
                         auto, operator, 'STAFF_LATE_CHECKOUT', now)
    return db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()


def extend_session_deadline(db, session_id, minutes=None, to_time=None, operator=None, now=None):
    """人工延长 / 重设 auto_end_at（突破 11:00 封顶）。

    - minutes: 相对当前 auto_end_at 增加（30/60/120 或任意正数）
    - to_time:  绝对时间（直接设为该时刻）
    延长后重置 auto_ended=0（截止被推到未来，桌局重新进行中），并记录审计。
    人工延长不受 11:00 封顶限制。
    """
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return None
    sess = dict(sess)
    now = now or datetime.now()
    old_value = sess.get('auto_end_at')
    if to_time is not None:
        new_dt = _parse_dt(to_time)
    elif minutes is not None and minutes > 0:
        new_dt = extend_auto_end_at(old_value, int(minutes))
    else:
        return sess
    if new_dt is None:
        return sess
    db.execute(
        "UPDATE sessions SET auto_end_at=?, auto_ended=0, auto_end_reason=NULL WHERE id=?",
        [new_dt.isoformat(timespec='seconds'), session_id]
    )
    write_end_time_audit(db, session_id, 'auto_end_at', old_value,
                         new_dt, operator, 'MANUAL_CORRECTION', now)
    return db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
