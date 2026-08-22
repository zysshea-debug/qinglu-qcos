"""QCOS 通宵桌动态截止 + 灵活实际结束时间 V3 —— 测试套件

设计目标（对齐需求）：
  - 三层时间模型：start_time / auto_end_at(系统默认保护截止,start+8h封顶11:00) / actual_end_time(人工确认真实结束)
  - 11:00 只是「系统默认自动截止线」，不是禁止玩家继续玩的硬上限；人工延长 / 事后确认可晚于 11:00
  - auto_end_at 到达只停止默认时间增长，绝不自动付款 / 自动结账
  - 统一 effective_end_time 优先级：actual_end_time > auto_end_at(已触发) > now
  - 离线补偿：服务重启后把越过 auto_end_at 的活跃通宵桌标 auto_ended=1（绝不成 now）
  - 审计：修改结束时间必须记录
  - 普通桌不受影响；商品/会员余额不被结束时间编辑影响；已 SETTLED 不可普通修改

安全约束：
  - 全部在临时数据库 / fake clock / mock 下进行，绝不触碰真实 qcos.db 与 .env
  - 不 commit、不 push
"""
import sys, os, json, tempfile, hashlib
from datetime import datetime, timedelta
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 测试专用环境变量（必须在 import config / models / app 之前注入，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_overnight_v3')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_overnight_v3')
os.environ.setdefault('QCOS_TEST_ADMIN_PASSWORD', 'test_admin_pw_overnight_v3')

import config
# 强制使用临时数据库，绝不要碰生产 qcos.db
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
_TMP_DB = _tmp.name
_tmp.close()
config.DB_PATH = _TMP_DB
config.PAYMENT['provider'] = 'mock'

import overnight
import models
from models import get_db, init_db
import billing
import app as app_module
from app import app

# ===== 确定性时钟注入（仅测试上下文，不改任何生产代码）=====
# 让 app 模块内的 datetime.now() 固定，避免端点测试中真实时间流逝造成漂移。
# 注意：overnight 模块使用自身的 datetime（真实），本测试的「精确时间」一律通过
#       向纯函数显式传入 now= 来控制，端点测试则依赖 auto_ended/actual 标志保证确定性。
FIXED_NOW = datetime(2026, 8, 19, 9, 0, 0)   # 真实店内的一个早晨时刻（贴近当前日期，长期不腐烂）


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FIXED_NOW
        return FIXED_NOW.astimezone(tz)


app_module.datetime = _FixedClock

client = app.test_client()
client_anon = app.test_client()   # 未登录客户端，用于权限测试

PASS = 0
FAIL = 0


def ok(name):
    global PASS
    PASS += 1
    print(f'  [OK] {name}')


def bad(name, msg):
    global FAIL
    FAIL += 1
    print(f'  [FAIL] {name}: {msg}')


@contextmanager
def dbconn():
    """打开一个 DB 连接，块结束时提交并关闭，异常时回滚并关闭（避免连接泄漏导致 database is locked）。"""
    db = get_db()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def login():
    r = client.post('/api/auth/login',
                    json={'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    assert r.status_code == 200, f'login {r.status_code}'


def add_player(name):
    r = client.post('/api/players', json={'name': name, 'phone': '', 'wechat': ''})
    return r.get_json()['id']


def reset_state():
    """关闭所有进行中的台桌并把机器置为空闲，避免机器被占用导致开台失败。"""
    with dbconn() as db:
        active = db.execute("SELECT id FROM sessions WHERE status='active'").fetchall()
        for s in active:
            db.execute("UPDATE sessions SET status='closed', end_time=? WHERE id=?",
                       [FIXED_NOW.isoformat(), s['id']])
        db.execute("UPDATE machines SET status='idle' WHERE status='active'")


def make_session(machine_id, start_time, is_overnight=False, auto_end_at=None,
                 auto_ended=0, actual_end_time=None, end_time_confirmed=0,
                 status='active', auto_end_reason=None):
    """直接插入一个 sessions 行（绕过端点），用于纯逻辑/DB 层测试。"""
    with dbconn() as db:
        cur = db.execute(
            """INSERT INTO sessions
               (machine_id, start_time, status, is_overnight, auto_end_at, auto_ended,
                auto_end_reason, actual_end_time, end_time_confirmed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                machine_id,
                start_time.isoformat() if isinstance(start_time, datetime) else start_time,
                status,
                1 if is_overnight else 0,
                auto_end_at.isoformat() if isinstance(auto_end_at, datetime) else auto_end_at,
                auto_ended,
                auto_end_reason,
                actual_end_time.isoformat() if isinstance(actual_end_time, datetime) else actual_end_time,
                end_time_confirmed,
            ]
        )
        sid = cur.lastrowid
    return sid


def get_session(sid):
    with dbconn() as db:
        row = db.execute('SELECT * FROM sessions WHERE id=?', [sid]).fetchone()
    return dict(row) if row else None


def _last_audit(sid):
    with dbconn() as db:
        row = db.execute(
            'SELECT * FROM end_time_audit WHERE session_id=? ORDER BY id DESC LIMIT 1', [sid]
        ).fetchone()
    return dict(row) if row else None


def iso(dt):
    return dt.isoformat(timespec='seconds')


# ============================================================
# 初始化
# ============================================================
init_db()
login()

print('\n===== 一、compute_auto_end_at 默认规则（start+8h 封顶 11:00）=====')

# 1
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 18, 23, 0))
    assert r == datetime(2026, 8, 19, 7, 0), r
    ok('23:00 开台 -> 次日 07:00')
except Exception as e:
    bad('compute 23:00', str(e))

# 2
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 18, 23, 30))
    assert r == datetime(2026, 8, 19, 7, 30), r
    ok('23:30 -> 07:30')
except Exception as e:
    bad('compute 23:30', str(e))

# 3
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 19, 0, 0))
    assert r == datetime(2026, 8, 19, 8, 0), r
    ok('00:00 -> 08:00')
except Exception as e:
    bad('compute 00:00', str(e))

# 4
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 19, 2, 30))
    assert r == datetime(2026, 8, 19, 10, 30), r
    ok('02:30 -> 10:30')
except Exception as e:
    bad('compute 02:30', str(e))

# 5
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 19, 3, 0))
    assert r == datetime(2026, 8, 19, 11, 0), r
    ok('03:00 -> 11:00（封顶生效）')
except Exception as e:
    bad('compute 03:00', str(e))

# 6
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 19, 4, 0))
    assert r == datetime(2026, 8, 19, 11, 0), r
    ok('04:00 -> 11:00（04+8=12 > 11，封顶）')
except Exception as e:
    bad('compute 04:00', str(e))

# 7
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 19, 2, 0))
    assert r == datetime(2026, 8, 19, 10, 0), r
    ok('02:00 -> 10:00')
except Exception as e:
    bad('compute 02:00', str(e))

print('\n===== 二、effective_end_time 统一优先级 =====')

# 8 actual 优先
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 1, 'actual_end_time': iso(datetime(2026, 8, 19, 9, 0))}
    r = overnight.effective_end_time(row, now=datetime(2026, 8, 19, 9, 30))
    assert r == datetime(2026, 8, 19, 9, 0), r
    ok('actual_end_time 优先（即便晚于 auto_end_at）')
except Exception as e:
    bad('effective actual 优先', str(e))

# 9 actual 晚于 11:00（突破封顶）仍被采用
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 1, 'actual_end_time': iso(datetime(2026, 8, 19, 13, 30))}
    r = overnight.effective_end_time(row, now=datetime(2026, 8, 19, 13, 30))
    assert r == datetime(2026, 8, 19, 13, 30), r
    ok('actual 可晚于 11:00（突破封顶，代表真实经营决定）')
except Exception as e:
    bad('effective actual 突破11点', str(e))

# 10 auto_ended 返回 auto_end_at
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 1, 'actual_end_time': None}
    r = overnight.effective_end_time(row, now=datetime(2026, 8, 19, 9, 0))
    assert r == datetime(2026, 8, 19, 7, 0), r
    ok('auto_ended 时返回 auto_end_at')
except Exception as e:
    bad('effective auto 分支', str(e))

# 11 now 分支（进行中、未超时、无 actual）
try:
    now = datetime(2026, 8, 19, 9, 0)
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 11, 0)),
           'auto_ended': 0, 'actual_end_time': None}
    r = overnight.effective_end_time(row, now=now)
    assert r == now, (r, now)
    ok('进行中未超时返回 now')
except Exception as e:
    bad('effective now 分支', str(e))

# 12 overnight 且已越过 auto_end_at（未 reaped）→ 不猜成 now，返回 auto_end_at（离线不猜）
try:
    now = datetime(2026, 8, 19, 13, 0)
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 0, 'actual_end_time': None}
    r = overnight.effective_end_time(row, now=now)
    assert r == datetime(2026, 8, 19, 7, 0), r
    ok('overnight 已超时未 reaped → 返回 auto_end_at（不猜成 now）')
except Exception as e:
    bad('effective 超时不猜', str(e))

# 13 auto_ended 但已设 actual → actual 盖过 auto
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 1, 'actual_end_time': iso(datetime(2026, 8, 19, 6, 0))}
    r = overnight.effective_end_time(row, now=datetime(2026, 8, 19, 9, 0))
    assert r == datetime(2026, 8, 19, 6, 0), r
    ok('auto_ended 但已设 actual → 取 actual（可早于 auto）')
except Exception as e:
    bad('effective actual盖过auto', str(e))

print('\n===== 三、延长（突破 11:00 封顶）=====')

# 14 extend_auto_end_at +30/+60/+120
try:
    base = datetime(2026, 8, 19, 11, 0)
    assert overnight.extend_auto_end_at(base, 30) == datetime(2026, 8, 19, 11, 30)
    assert overnight.extend_auto_end_at(base, 60) == datetime(2026, 8, 19, 12, 0)
    assert overnight.extend_auto_end_at(base, 120) == datetime(2026, 8, 19, 13, 0)
    ok('+30/+60/+120 分钟延长')
except Exception as e:
    bad('extend +30/60/120', str(e))

# 15 延长突破 11:00 封顶
try:
    base = datetime(2026, 8, 19, 11, 0)
    r = overnight.extend_auto_end_at(base, 120)
    assert r == datetime(2026, 8, 19, 13, 0) and r.hour > 11, r
    ok('延长突破 11:00 封顶（13:00）')
except Exception as e:
    bad('extend 突破封顶', str(e))

print('\n===== 四、session_overnight_status 状态展示 =====')

# 16 overnight_active
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 11, 0)),
           'auto_ended': 0, 'actual_end_time': None, 'end_time_confirmed': 0}
    key, text, deadline = overnight.session_overnight_status(row, now=datetime(2026, 8, 19, 9, 0))
    assert key == 'overnight_active', key
    assert deadline == datetime(2026, 8, 19, 11, 0)
    ok('overnight_active（进行中）')
except Exception as e:
    bad('status active', str(e))

# 17 pending_confirm
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 1, 'actual_end_time': None, 'end_time_confirmed': 0}
    key, text, deadline = overnight.session_overnight_status(row, now=datetime(2026, 8, 19, 9, 0))
    assert key == 'pending_confirm', key
    ok('pending_confirm（待确认结束时间）')
except Exception as e:
    bad('status pending', str(e))

# 18 ended_confirmed
try:
    row = {'is_overnight': 1, 'auto_end_at': iso(datetime(2026, 8, 19, 7, 0)),
           'auto_ended': 1, 'actual_end_time': iso(datetime(2026, 8, 19, 9, 0)),
           'end_time_confirmed': 1}
    key, text, deadline = overnight.session_overnight_status(row, now=datetime(2026, 8, 19, 9, 30))
    assert key == 'ended_confirmed', key
    ok('ended_confirmed（已确认结束）')
except Exception as e:
    bad('status ended', str(e))

print('\n===== 五、离线补偿 reap_overnight_sessions =====')

# 19 reap 标记超时桌、不改 auto_end_at、不写 now
try:
    auto = datetime(2026, 8, 19, 7, 0)
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=auto, auto_ended=0, status='active')
    now = datetime(2026, 8, 19, 13, 0)
    with dbconn() as db:
        cnt = overnight.reap_overnight_sessions(db, now=now)
    row = get_session(sid)
    assert cnt == 1, cnt
    assert row['auto_ended'] == 1, row
    assert row['auto_end_reason'] == 'AUTO_DEADLINE_REACHED', row['auto_end_reason']
    assert row['auto_end_at'] == iso(auto), row['auto_end_at']
    assert row['status'] == 'active', 'reap 不应关闭台桌'
    assert row['fee'] is None, 'reap 绝不自动收款'
    ok('reap：超时活跃通宵桌 → auto_ended=1，auto_end_at 不变（非 now），不收款/不关台')
except Exception as e:
    bad('reap 标记超时', str(e))

# 20 reap 幂等
try:
    auto = datetime(2026, 8, 19, 7, 0)
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=auto, auto_ended=0, status='active')
    now = datetime(2026, 8, 19, 13, 0)
    with dbconn() as db:
        c1 = overnight.reap_overnight_sessions(db, now=now)
    with dbconn() as db:
        c2 = overnight.reap_overnight_sessions(db, now=now)
    row = get_session(sid)
    assert c1 == 1 and c2 == 0, (c1, c2)
    assert row['auto_end_at'] == iso(auto), '二次 reap 不应改变 auto_end_at'
    ok('reap 幂等（二次返回 0，不改 deadline）')
except Exception as e:
    bad('reap 幂等', str(e))

# 21 reap 忽略非通宵 / 未超时
try:
    s_normal = make_session(1, datetime(2026, 8, 19, 8, 0), is_overnight=False,
                            auto_end_at=None, auto_ended=0, status='active')
    future_auto = datetime(2026, 8, 19, 23, 0)
    s_future = make_session(2, datetime(2026, 8, 19, 15, 0), is_overnight=True,
                            auto_end_at=future_auto, auto_ended=False, status='active')
    now = datetime(2026, 8, 19, 20, 0)
    with dbconn() as db:
        cnt = overnight.reap_overnight_sessions(db, now=now)
    assert cnt == 0, cnt
    assert get_session(s_normal)['auto_ended'] == 0
    assert get_session(s_future)['auto_ended'] == 0
    ok('reap 忽略非通宵与尚未超时桌')
except Exception as e:
    bad('reap 忽略', str(e))

print('\n===== 六、ensure_auto_end_at =====')

# 22 通宵设置 + 幂等
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=False, auto_end_at=None)
    with dbconn() as db:
        overnight.ensure_auto_end_at(db, sid, True, datetime(2026, 8, 18, 23, 0))
    row = get_session(sid)
    assert row['is_overnight'] == 1
    assert row['auto_end_at'] == iso(datetime(2026, 8, 19, 7, 0)), row['auto_end_at']
    # 幂等：再次调用不应覆盖已设置的 deadline
    with dbconn() as db:
        overnight.ensure_auto_end_at(db, sid, True, datetime(2026, 8, 18, 23, 0))
    assert get_session(sid)['auto_end_at'] == iso(datetime(2026, 8, 19, 7, 0))
    ok('ensure：通宵写入默认截止；二次调用不覆盖（幂等）')
except Exception as e:
    bad('ensure 通宵', str(e))

# 23 非通宵清空
try:
    sid = make_session(1, datetime(2026, 8, 19, 8, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 16, 0), auto_ended=1)
    with dbconn() as db:
        overnight.ensure_auto_end_at(db, sid, False, datetime(2026, 8, 19, 8, 0))
    row = get_session(sid)
    assert row['is_overnight'] == 0 and row['auto_end_at'] is None and row['auto_ended'] == 0, row
    ok('ensure：非通宵清空 auto_end_at / auto_ended（普通桌不受影响）')
except Exception as e:
    bad('ensure 非通宵清空', str(e))

print('\n===== 七、set_actual_end_time / confirm / extend（DB 层）=====')

# 24 set_actual 写入实际 + 确认 + 审计
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    actual = datetime(2026, 8, 19, 9, 30)
    with dbconn() as db:
        overnight.set_actual_end_time(db, sid, actual, '九哥', reason='OVERNIGHT_LATE_PLAY', now=FIXED_NOW)
    row = get_session(sid)
    assert row['actual_end_time'] == iso(actual), row['actual_end_time']
    assert row['end_time_confirmed'] == 1
    assert row['end_time_confirmed_by'] == '九哥'
    aud = _last_audit(sid)
    assert aud and aud['field'] == 'actual_end_time' and aud['new_end_time'] == iso(actual), aud
    ok('set_actual：写入实际结束 + 确认 + 审计')
except Exception as e:
    bad('set_actual', str(e))

# 25 set_actual 可晚于 11:00
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    actual = datetime(2026, 8, 19, 13, 30)
    with dbconn() as db:
        overnight.set_actual_end_time(db, sid, actual, '九哥', reason='OVERNIGHT_LATE_PLAY', now=FIXED_NOW)
    assert get_session(sid)['actual_end_time'] == iso(actual)
    ok('set_actual：实际结束时间可晚于 11:00（突破封顶）')
except Exception as e:
    bad('set_actual 突破封顶', str(e))

# 26 非法 reason 归为 OTHER
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    with dbconn() as db:
        overnight.set_actual_end_time(db, sid, datetime(2026, 8, 19, 8, 0), '九哥', reason='BOGUS', now=FIXED_NOW)
    aud = _last_audit(sid)
    assert aud['reason'] == 'OTHER', aud
    ok('非法审计原因归为 OTHER')
except Exception as e:
    bad('reason 归一', str(e))

# 27 confirm_default：默认把 auto_end_at 作为实际结束
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    with dbconn() as db:
        overnight.confirm_default_end_time(db, sid, '店员A', now=FIXED_NOW)
    row = get_session(sid)
    assert row['actual_end_time'] == iso(datetime(2026, 8, 19, 7, 0)), row['actual_end_time']
    assert row['end_time_confirmed'] == 1
    aud = _last_audit(sid)
    assert aud['reason'] == 'STAFF_LATE_CHECKOUT' and aud['new_end_time'] == iso(datetime(2026, 8, 19, 7, 0)), aud
    ok('confirm_default：默认把 auto_end_at 作为实际结束（原因 STAFF_LATE_CHECKOUT）')
except Exception as e:
    bad('confirm_default', str(e))

# 28 extend_session_deadline（分钟）重置 auto_ended + 审计
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    with dbconn() as db:
        overnight.extend_session_deadline(db, sid, minutes=120, operator='九哥', now=FIXED_NOW)
    row = get_session(sid)
    assert row['auto_end_at'] == iso(datetime(2026, 8, 19, 9, 0)), row['auto_end_at']
    assert row['auto_ended'] == 0, '延长后应重新进行中'
    aud = _last_audit(sid)
    assert aud['field'] == 'auto_end_at' and aud['new_end_time'] == iso(datetime(2026, 8, 19, 9, 0)), aud
    ok('extend（分钟）：auto_end_at 推后 + 重置 auto_ended + 审计')
except Exception as e:
    bad('extend 分钟', str(e))

# 29 extend（绝对时间）突破封顶
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    target = datetime(2026, 8, 19, 14, 0)
    with dbconn() as db:
        overnight.extend_session_deadline(db, sid, to_time=target, operator='九哥', now=FIXED_NOW)
    row = get_session(sid)
    assert row['auto_end_at'] == iso(target) and row['auto_ended'] == 0, row
    ok('extend（绝对时间）：可设到 14:00（突破 11:00）')
except Exception as e:
    bad('extend 绝对', str(e))

print('\n===== 八、审计记录字段完整性 =====')

# 30 审计字段
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    with dbconn() as db:
        overnight.set_actual_end_time(db, sid, datetime(2026, 8, 19, 10, 0), '九哥',
                                      reason='MANUAL_CORRECTION', now=FIXED_NOW)
    aud = _last_audit(sid)
    need = {'session_id', 'field', 'old_end_time', 'new_end_time', 'operator', 'reason', 'changed_at'}
    missing = need - set(aud.keys())
    assert not missing, f'缺失字段 {missing}'
    assert aud['session_id'] == sid and aud['operator'] == '九哥' and aud['reason'] == 'MANUAL_CORRECTION'
    assert aud['changed_at'] == iso(FIXED_NOW), aud['changed_at']
    ok('审计记录含全部字段（session/field/old/new/operator/reason/changed_at）')
except Exception as e:
    bad('audit 字段', str(e))

print('\n===== 九、跨月 / 跨年 / 时区（Asia/Shanghai 本地朴素时间）=====')

# 31 跨月
try:
    r = overnight.compute_auto_end_at(datetime(2026, 1, 31, 23, 0))
    assert r == datetime(2026, 2, 1, 7, 0), r
    ok('跨月：01-31 23:00 -> 02-01 07:00')
except Exception as e:
    bad('跨月', str(e))

# 32 跨年
try:
    r = overnight.compute_auto_end_at(datetime(2026, 12, 31, 23, 0))
    assert r == datetime(2027, 1, 1, 7, 0), r
    ok('跨年：12-31 23:00 -> 次年 01-01 07:00')
except Exception as e:
    bad('跨年', str(e))

# 33 时区：本地朴素时间，00:00 -> 08:00（无 UTC 偏移）
try:
    r = overnight.compute_auto_end_at(datetime(2026, 8, 19, 0, 0))
    assert r == datetime(2026, 8, 19, 8, 0), r
    # 朴素时间，未做任何 astimezone；确认不存在 UTC 偏移导致的 08:00->16:00 之类错误
    ok('Asia/Shanghai 本地朴素时间：00:00 -> 08:00（无 UTC 偏移）')
except Exception as e:
    bad('时区', str(e))

print('\n===== 十、编辑结束时间不影响商品/会员；重算账单 =====')

# 34 商品/会员不受影响
try:
    sid = make_session(1, datetime(2026, 8, 18, 23, 0), is_overnight=True,
                       auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    with dbconn() as db:
        db.execute("INSERT INTO product_sales (session_id, product_name, price, quantity, total, status) "
                   "VALUES (?, ?, ?, ?, ?, 'UNSETTLED')", [sid, '可乐', 5, 2, 10])
        db.execute("INSERT INTO players (name) VALUES (?)", ['临时会员X'])
        pid = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        db.execute("INSERT INTO members (player_id, balance, total_recharge, total_spent, status) "
                   "VALUES (?, ?, ?, ?, 'active')", [pid, 200.0, 200.0, 0])
    with dbconn() as db:
        overnight.set_actual_end_time(db, sid, datetime(2026, 8, 19, 9, 0), '九哥', now=FIXED_NOW)
    with dbconn() as db:
        ps = db.execute('SELECT COUNT(*) c, SUM(total) t FROM product_sales WHERE session_id=?', [sid]).fetchone()
        m = db.execute('SELECT balance FROM members WHERE player_id=?', [pid]).fetchone()
    assert ps['c'] == 1 and ps['t'] == 10, ps
    assert m['balance'] == 200.0, m
    ok('编辑结束时间不改动商品销售 / 会员余额')
except Exception as e:
    bad('商品会员不受影响', str(e))

# 35 重算：实际结束时间改变 → 账单重算（跨夜费率变化）
try:
    start = datetime(2026, 8, 18, 23, 0)
    with dbconn() as db:
        settings = models.get_all_settings(db)
    # 默认截止（第一晚）vs 改为次日 08:00（跨两晚 + 一整天），费用必然不同且后者更贵
    fee_one = billing.calculate_fee('8port', start, datetime(2026, 8, 19, 7, 0), settings, is_overnight=True)[0]
    fee_two = billing.calculate_fee('8port', start, datetime(2026, 8, 20, 8, 0), settings, is_overnight=True)[0]
    assert fee_two > fee_one, (fee_one, fee_two)  # 改为跨夜 → 账单更贵，证明按新 end 重算

    # 端到端：effective_end_time 采用 actual，结账系统据此重算
    sid = make_session(1, start, is_overnight=True, auto_end_at=datetime(2026, 8, 19, 7, 0), auto_ended=1)
    with dbconn() as db:
        overnight.set_actual_end_time(db, sid, datetime(2026, 8, 20, 8, 0), '九哥', now=FIXED_NOW)
    row = get_session(sid)
    end = overnight.effective_end_time(row)
    assert end == datetime(2026, 8, 20, 8, 0), end
    ok('重算：实际结束时间改为跨夜 → 账单按 changed actual 重算（费用随 end 变化）')
except Exception as e:
    bad('重算', str(e))

print('\n===== 十一、四口 / 八口 两类机器均生效 =====')

# 36 八口 & 四口开台均生成 auto_end_at
try:
    reset_state()
    # 八口机1
    r1 = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': (FIXED_NOW - timedelta(hours=10)).isoformat(),
        'players': [{'name': '八口客', 'is_overnight': True,
                     'start_time': (FIXED_NOW - timedelta(hours=10)).isoformat()}]
    })
    s1 = get_session(r1.get_json()['id'])
    exp1 = iso(overnight.compute_auto_end_at(FIXED_NOW - timedelta(hours=10)))
    assert s1['is_overnight'] == 1 and s1['auto_end_at'] == exp1, (s1['auto_end_at'], exp1)
    # 四口机1
    reset_state()
    r3 = client.post('/api/sessions', json={
        'machine_id': 3, 'start_time': (FIXED_NOW - timedelta(hours=10)).isoformat(),
        'players': [{'name': '四口客', 'is_overnight': True,
                     'start_time': (FIXED_NOW - timedelta(hours=10)).isoformat()}]
    })
    s3 = get_session(r3.get_json()['id'])
    exp3 = iso(overnight.compute_auto_end_at(FIXED_NOW - timedelta(hours=10)))
    assert s3['is_overnight'] == 1 and s3['auto_end_at'] == exp3, (s3['auto_end_at'], exp3)
    ok('八口机 & 四口机开通宵桌均生成 auto_end_at')
except Exception as e:
    bad('四口八口', str(e))

print('\n===== 十二、端点：待确认门禁 / 调整 / 确认 / 延长 / 不自动收钱 =====')

# 37 待确认：auto_ended 且未确认 → preview 报 needs_end_time_confirmation，end_time=auto_end_at
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '待确认客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    # 直接把该桌标成自动截止（模拟越过 11:00 的无人值班状态）
    with dbconn() as db:
        db.execute("UPDATE sessions SET auto_ended=1, auto_end_reason='AUTO_DEADLINE_REACHED' WHERE id=?", [sid])
    rp = client.get(f'/api/sessions/{sid}/preview').get_json()
    assert rp['needs_end_time_confirmation'] is True, rp
    assert rp['end_time'] == rp['auto_end_at'], (rp['end_time'], rp['auto_end_at'])
    assert rp['auto_ended'] is True
    ok('待确认：preview 返回 needs_end_time_confirmation，end_time=auto_end_at（不自动收钱）')
except Exception as e:
    bad('待确认门禁', str(e))

# 38 结账门禁：auto_ended 未确认 → 409 NEEDS_END_TIME_CONFIRMATION
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '结账拦截客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    with dbconn() as db:
        db.execute("UPDATE sessions SET auto_ended=1, auto_end_reason='AUTO_DEADLINE_REACHED' WHERE id=?", [sid])
    # 没有 auto_end_confirmed
    rc = client.post(f'/api/sessions/{sid}/close', json={'payment_method': 'cash', 'product_total': 0})
    assert rc.status_code == 409 and rc.get_json().get('error') == 'NEEDS_END_TIME_CONFIRMATION', (rc.status_code, rc.get_json())
    # 带 auto_end_confirmed=true 可越过门禁（后续走正常结账流程）
    rc2 = client.post(f'/api/sessions/{sid}/close', json={'payment_method': 'cash', 'product_total': 0, 'auto_end_confirmed': True})
    assert rc2.status_code != 409, (rc2.status_code, rc2.get_json())
    ok('结账门禁：未确认 → 409；带 auto_end_confirmed → 越过')
except Exception as e:
    bad('结账门禁', str(e))

# 39 调整端点：写入 actual，preview 不再 needs_confirmation
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '调整客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    actual = (FIXED_NOW + timedelta(hours=2)).isoformat()  # 晚于 11:00 封顶
    ra = client.post(f'/api/sessions/{sid}/adjust-end-time',
                     json={'actual_end_time': actual, 'reason': 'OVERNIGHT_LATE_PLAY'})
    assert ra.status_code == 200, (ra.status_code, ra.get_json())
    rp = client.get(f'/api/sessions/{sid}/preview').get_json()
    assert rp['needs_end_time_confirmation'] is False, rp
    assert rp['actual_end_time'] == actual, (rp['actual_end_time'], actual)
    ok('调整端点：写入 actual（可晚于11:00），preview 不再待确认')
except Exception as e:
    bad('调整端点', str(e))

# 40 确认端点：默认把 auto_end_at 作为实际结束
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '确认客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    with dbconn() as db:
        db.execute("UPDATE sessions SET auto_ended=1, auto_end_reason='AUTO_DEADLINE_REACHED' WHERE id=?", [sid])
    rc = client.post(f'/api/sessions/{sid}/confirm-end-time', json={})
    assert rc.status_code == 200, (rc.status_code, rc.get_json())
    row = get_session(sid)
    assert row['end_time_confirmed'] == 1 and row['actual_end_time'] == row['auto_end_at'], row
    ok('确认端点：默认 actual_end_time = auto_end_at 且已确认')
except Exception as e:
    bad('确认端点', str(e))

# 41 延长端点：突破 11:00 封顶并重置 auto_ended
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '延长客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    with dbconn() as db:
        db.execute("UPDATE sessions SET auto_ended=1, auto_end_reason='AUTO_DEADLINE_REACHED' WHERE id=?", [sid])
    re = client.post(f'/api/sessions/{sid}/extend', json={'minutes': 120})
    assert re.status_code == 200, (re.status_code, re.get_json())
    row = get_session(sid)
    new_auto = datetime.fromisoformat(row['auto_end_at'])
    base = overnight.compute_auto_end_at(sstart)
    assert new_auto == base + timedelta(minutes=120), (new_auto, base)
    assert row['auto_ended'] == 0, '延长后重新进行中'
    ok('延长端点：分钟延长突破封顶并重置 auto_ended')
except Exception as e:
    bad('延长端点', str(e))

print('\n===== 十三、普通桌不受影响；已 SETTLED 不可普通修改；权限 =====')

# 42 普通桌：auto_end_at 为空，preview 正常
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=3)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '普通客', 'is_overnight': False, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    rp = client.get(f'/api/sessions/{sid}/preview').get_json()
    assert rp['is_overnight'] is False and rp['auto_end_at'] is None, rp
    assert rp['needs_end_time_confirmation'] is False, rp
    ok('普通桌：无 auto_end_at，不受通宵逻辑影响')
except Exception as e:
    bad('普通桌不受影响', str(e))

# 43 已 SETTLED（closed）不可普通修改
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '已结客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    # 关闭台桌（模拟已结账）
    with dbconn() as db:
        db.execute("UPDATE sessions SET status='closed', end_time=?, actual_end_time=?, end_time_confirmed=1 WHERE id=?",
                   [sstart.isoformat(), sstart.isoformat(), sid])
    ra = client.post(f'/api/sessions/{sid}/adjust-end-time',
                     json={'actual_end_time': (FIXED_NOW + timedelta(hours=1)).isoformat()})
    assert ra.status_code == 400, (ra.status_code, ra.get_json())
    ok('已 SETTLED 不可普通修改结束时间（端点 400）')
except Exception as e:
    bad('已SETTLED不可改', str(e))

# 44 权限：未登录调用调整/确认/延长 → 401
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '权限客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    for ep in ['adjust-end-time', 'confirm-end-time', 'extend']:
        rr = client_anon.post(f'/api/sessions/{sid}/{ep}', json={})
        assert rr.status_code == 401, (ep, rr.status_code, rr.get_json())
    ok('权限：未登录调用调整/确认/延长 → 401（需鉴权）')
except Exception as e:
    bad('权限门禁', str(e))

print('\n===== 十四、preview 与 checkout 金额一致（确认后）=====')

# 45 单人 preview 与单人 checkout 金额一致
try:
    reset_state()
    sstart = FIXED_NOW - timedelta(hours=10)
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': sstart.isoformat(),
        'players': [{'name': '一致客', 'is_overnight': True, 'start_time': sstart.isoformat()}]
    })
    sid = r.get_json()['id']
    # 确认实际结束时间（使 end_time 确定，preview 与 checkout 同口径）
    with dbconn() as db:
        db.execute("UPDATE sessions SET auto_ended=1, auto_end_reason='AUTO_DEADLINE_REACHED' WHERE id=?", [sid])
    client.post(f'/api/sessions/{sid}/confirm-end-time', json={})
    # 取首个在玩玩家
    sp_id = client.get(f'/api/sessions/{sid}/preview').get_json()['players'][0]['id']
    prev = client.get(f'/api/sessions/{sid}/players/{sp_id}/preview').get_json()
    chk = client.post(f'/api/sessions/{sid}/players/{sp_id}/checkout', json={
        'payment_method': 'cash', 'product_total': 0, 'start_time': sstart.isoformat(),
        'manual_discount_type': None, 'manual_discount_value': 0, 'auto_end_confirmed': True
    }).get_json()
    assert abs((prev.get('fee') or 0) - (chk.get('fee') or 0)) < 0.01, (prev.get('fee'), chk.get('fee'))
    ok('preview 与 checkout 金额一致（确认实际结束时间后同口径）')
except Exception as e:
    bad('preview-checkout一致', str(e))

print(f'\n结果: {PASS} 通过, {FAIL} 失败')
sys.exit(1 if FAIL else 0)
