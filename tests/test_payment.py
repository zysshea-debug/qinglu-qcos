"""支付确认模块端到端测试（mock provider，验证门禁逻辑）"""
import sys, os, json, tempfile
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import config
# 强制使用临时数据库，绝不要碰生产 qcos.db
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name
config.PAYMENT['provider'] = 'mock'   # 测试用 mock，无需凭证

import app as app_module
from app import app, g
from models import get_db, init_db
import payment as payment_module
payment_module._PROVIDERS.clear()

# ===== 确定性时钟注入（仅测试上下文，不改任何生产代码）=====
# preview 与 checkout 均以 app 模块的 datetime.now() 作为计费 end_time；
# 若不固定，两次调用之间真实时间流逝会让计费金额漂移（本测试曾因硬编码
# 5 天前的开始时间而漂移，导致金额校验失败）。这里把 app 模块内的
# datetime.now() 固定到「当前整分钟」，使 preview 与 checkout 使用
# 同一个确定性时间基准，金额必然一致。时间贴近真实日期，长期运行不腐烂。
FIXED_NOW = datetime.now().replace(second=0, microsecond=0)


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FIXED_NOW
        return FIXED_NOW.astimezone(tz)


app_module.datetime = _FixedClock

client = app.test_client()
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

def login():
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    assert r.status_code == 200, f'login {r.status_code}'

def add_player(name):
    r = client.post('/api/players', json={'name': name, 'phone': '', 'wechat': ''})
    return r.get_json()['id']

def start_session(machine_id, player_name, player_id, start_iso):
    r = client.post('/api/sessions', json={
        'machine_id': machine_id, 'start_time': start_iso,
        'players': [{'name': player_name, 'player_id': player_id, 'start_time': start_iso}]
    })
    data = r.get_json()
    sid = data['id']
    # 取该台桌首个在玩玩家
    r2 = client.get(f'/api/sessions/{sid}/preview')
    sp = r2.get_json()['players'][0]
    sp_id = sp['id']
    # 金额必须取「单人预览」，与单人结账（/players/<sp_id>/checkout）完全同口径：
    # 同样的 start_time、同样的 is_overnight（均回退到 session_players 表值）、
    # 同样的 end_time（注入的固定时钟）。若取整台预览的 fee（默认 is_overnight=True），
    # 在通宵时段会与单人结账的正常费率产生分歧，导致支付金额校验失败。
    r3 = client.get(f'/api/sessions/{sid}/players/{sp_id}/preview')
    prev = r3.get_json()
    amount = round((prev.get('fee') or 0) + (prev.get('product_total') or 0), 2)
    return sid, sp_id, amount

# ---------- 0. 初始化 ----------
init_db()
login()

# 复位：关闭所有进行中的台桌并把机器置为空闲，避免机器被占用导致开台失败
def reset_state():
    db = get_db()
    active = db.execute("SELECT id FROM sessions WHERE status='active'").fetchall()
    for s in active:
        db.execute("UPDATE sessions SET status='closed', end_time=? WHERE id=?",
                   [datetime.now().isoformat(), s['id']])
    db.execute("UPDATE machines SET status='idle' WHERE status='active'")
    db.commit()
    db.close()

reset_state()

# ---------- 1. 支付确认开关 ----------
r = client.get('/api/payment/status')
d = r.get_json()
if r.status_code == 200 and d.get('enabled') is True and d.get('provider') == 'mock':
    ok('支付确认开关开启(mock)')
else:
    bad('支付确认开关', f'{r.status_code} {d}')

# ---------- 2. 被扫支付确认成功 ----------
# 开始时间取固定时钟前 30 分钟：贴近当前、时长稳定（首小时内最低消费），
# 且 preview 与 checkout 使用同一个 FIXED_NOW 基准，金额必然一致。
start = (FIXED_NOW - timedelta(minutes=30)).isoformat()
pid = add_player('支付测试')
sid, spid, amount = start_session(1, '支付测试', pid, start)
r = client.post('/api/payment/micropay', json={'auth_code': '134567890123456', 'amount': amount, 'method': 'scan_wechat'})
mp = r.get_json()
if r.status_code == 200 and mp.get('status') == 'SUCCESS' and mp.get('out_trade_no'):
    ok('被扫支付确认成功')
    payment_ref = mp['out_trade_no']
else:
    bad('被扫支付确认', f'{r.status_code} {mp}')
    payment_ref = None

# ---------- 3. 凭确认流水结账成功 ----------
# 注意: 不显式传 is_overnight，让 checkout 与 preview 一样回退到
# session_players 表里的值（开台时按时段自动判定），保证两侧费率口径一致。
# 此前测试显式传 is_overnight=False，在通宵时段（00:00-08:00）会与 preview
# 的通宵包夜费率产生分歧，导致「支付金额与结账金额不一致」。
r = client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
    'payment_method': 'scan_wechat', 'product_total': 0,
    'start_time': start,
    'manual_discount_type': None, 'manual_discount_value': 0,
    'payment_ref': payment_ref
})
if r.status_code == 200 and r.get_json().get('payment_method') == 'scan_wechat':
    ok('凭确认流水结账成功')
else:
    bad('凭确认流水结账', f'{r.status_code} {r.get_json()}')

# ---------- 4. 未确认支付直接结账被拒（门禁） ----------
pid2 = add_player('支付拦截')
sid2, spid2, _ = start_session(2, '支付拦截', pid2, start)
r = client.post(f'/api/sessions/{sid2}/players/{spid2}/checkout', json={
    'payment_method': 'scan_wechat', 'product_total': 0,
    'start_time': start, 'is_overnight': False,
    'manual_discount_type': None, 'manual_discount_value': 0
})
if r.status_code == 400 and '未确认' in (r.get_json().get('error') or ''):
    ok('未确认支付结账被拒(门禁生效)')
else:
    bad('门禁未生效', f'{r.status_code} {r.get_json()}')

# ---------- 5. 关闭支付确认后旧逻辑仍可用 ----------
config.PAYMENT['provider'] = None
payment_module._PROVIDERS.clear()
pid3 = add_player('旧逻辑')
sid3, spid3, _ = start_session(3, '旧逻辑', pid3, start)
r = client.post(f'/api/sessions/{sid3}/players/{spid3}/checkout', json={
    'payment_method': 'scan_wechat', 'product_total': 0,
    'start_time': start, 'is_overnight': False,
    'manual_discount_type': None, 'manual_discount_value': 0
})
# 旧逻辑无 payment_ref 也应成功（仅靠收银员确认）
if r.status_code == 200:
    ok('关闭确认后旧逻辑结账成功')
else:
    bad('旧逻辑结账', f'{r.status_code} {r.get_json()}')
config.PAYMENT['provider'] = 'mock'
payment_module._PROVIDERS.clear()

print(f'\n结果: {PASS} 通过, {FAIL} 失败')
sys.exit(1 if FAIL else 0)
