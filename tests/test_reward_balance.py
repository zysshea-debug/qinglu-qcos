"""活动奖励金（主动约桌抽奖）端到端测试

覆盖验收场景：
  A 台费100+免台费 → +100
  B 台费150+免台费 → +100（封顶）
  C 台费80+半价 → +40
  D 台费200+半价 → +50（封顶）
  E 台费100+8折 → +20
  F 台费300+8折 → +30（封顶）
  G 奖励金30+台费50 → 奖励金扣30、剩余20走原支付逻辑
  H 奖励金30+饮料20 → 饮料不得扣奖励金
  I 同一订单台费+饮料 → 仅台费部分可用奖励金
  J 历史会员储值余额、订单、流水不得变化

运行方式：python tests/test_reward_balance.py（脚本式）或 pytest tests/test_reward_balance.py
使用临时数据库，绝不触碰生产 qcos.db。
"""
import os
import sys
import sqlite3
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.DB_PATH = _tmp.name  # 同步绑定，避免写进其他模块的临时库
models.init_db()
import app as APP
from billing import calculate_reward, calculate_fee

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


def db():
    d = sqlite3.connect(config.DB_PATH)
    d.row_factory = sqlite3.Row
    return d


def reset_state():
    """关闭所有进行中台桌并释放机器，使各测试相互独立（脚本模式共享同一临时库）。"""
    d = db()
    for s in d.execute("SELECT id FROM sessions WHERE status='active'").fetchall():
        d.execute("UPDATE sessions SET status='closed', end_time=? WHERE id=?",
                  [datetime.now().isoformat(), s['id']])
    d.execute("UPDATE machines SET status='idle' WHERE status='active'")
    d.commit()
    d.close()


def login():
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    assert r.status_code == 200, r.data


def add_player(name):
    r = client.post('/api/players', json={'name': name})
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def add_member(player_id, balance):
    r = client.post('/api/members', json={
        'player_id': player_id,
        'initial_balance': balance,
        'payment_method': 'cash'
    })
    assert r.status_code == 201, r.data
    return r.get_json()['id']


def start_session(machine_id, player_id, player_name, start_time):
    r = client.post('/api/sessions', json={
        'machine_id': machine_id,
        'start_time': start_time,
        'players': [{'name': player_name, 'player_id': player_id}]
    })
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    d = db()
    spid = d.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    d.close()
    return sid, spid


def grant_reward(member_id, reward_type, session_id=None, session_player_id=None, fee=None, draw_id=None):
    """发放奖励金。

    Final Hardening 后 /api/rewards/grant 不再信任前端 fee：
    台费由后端根据 session_id/session_player_id 用正式计费引擎计算。
    因此测试必须提供真实进行中桌局；fee 仅在需要触发 FEE_STALE 时显式传入。
    """
    payload = {'member_id': member_id, 'reward_type': reward_type}
    if session_id is not None:
        payload['session_id'] = session_id
    if session_player_id is not None:
        payload['session_player_id'] = session_player_id
    if fee is not None:
        payload['fee'] = fee
    if draw_id is not None:
        payload['draw_id'] = draw_id
    return client.post('/api/rewards/grant', json=payload)


def checkout(sid, spid, payment_method='cash', member_id=None, product_total=0):
    return client.post(f'/api/sessions/{sid}/players/{spid}/checkout', json={
        'payment_method': payment_method,
        'member_id': member_id,
        'product_total': product_total,
        'discount_id': None,
        'start_time': None,
        'is_overnight': False,
        'manual_discount_type': '',
        'manual_discount_value': 0
    })


def member_row(member_id):
    d = db()
    m = d.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
    d.close()
    return dict(m) if m else None


def expected_fee(machine_id, start_iso, is_overnight=0):
    """用正式计费引擎计算当前应收台费。

    台费取决于「当前时刻所处时段」：会话若跨越 00:00/08:00/12:00 会被拆成多段，
    每段各自计最低一小时，因此不能写死金额（例如 2.5h 在跨午夜时为 70 而非 50）。
    """
    d = db()
    mtype = d.execute('SELECT type FROM machines WHERE id=?', [machine_id]).fetchone()['type']
    settings = APP.get_all_settings(d)
    d.close()
    fee, _ = calculate_fee(mtype, datetime.fromisoformat(start_iso), datetime.now(),
                           settings, is_overnight=bool(is_overnight))
    return round(fee, 2)


# ===== 单元测试：发放金额计算（验收 A-F） =====

def test_calculate_reward_amounts():
    print('\n[1] calculate_reward 金额计算（验收 A-F）')
    cases = [
        ('free', 100, 100.0),    # A
        ('free', 150, 100.0),    # B 封顶
        ('half', 80, 40.0),      # C
        ('half', 200, 50.0),     # D 封顶
        ('discount', 100, 20.0), # E
        ('discount', 300, 30.0), # F 封顶
    ]
    all_ok = True
    for rtype, fee, expected in cases:
        got = calculate_reward(fee, rtype)
        if abs(got - expected) > 0.001:
            bad(f'{rtype} fee={fee}', f'期望{expected} 实际{got}')
            all_ok = False
    if all_ok:
        ok('A-F 六组发放金额全部正确')
    # 边界：0/负数/未知类型
    assert calculate_reward(0, 'free') == 0.0
    assert calculate_reward(-5, 'half') == 0.0
    assert calculate_reward(100, 'unknown') == 0.0
    ok('边界：0/负数/未知类型返回0')


# ===== API 测试：发放（含封顶与流水） =====

def test_grant_api():
    reset_state()
    print('\n[2] 发放接口 /api/rewards/grant（后端权威台费，不信任 client fee）')
    pid = add_player('发放测试')
    mid = add_member(pid, 0)

    # 无效类型
    r = grant_reward(mid, 'unknown')
    if r.status_code != 400:
        bad('无效类型拒绝', f'应400，实际{r.status_code}')
        return
    ok('无效类型被拒绝')

    # 无进行中桌局：不得凭空发奖
    r = grant_reward(mid, 'free')
    if r.status_code != 400:
        bad('无桌局拒绝发奖', f'应400，实际{r.status_code} {r.data}')
        return
    ok('无进行中桌局 → 拒绝发放')

    # 开桌 2 小时 → 后端按计费引擎算 server_fee 并发放
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    sid, spid = start_session(1, pid, '发放测试', start)
    r = grant_reward(mid, 'free', session_id=sid, session_player_id=spid)
    if r.status_code != 201:
        bad('发放成功', f'{r.status_code} {r.data}')
        return
    j = r.get_json()
    server_fee = j.get('server_fee')
    expected = calculate_reward(server_fee, 'free')
    if server_fee is None or server_fee <= 0:
        bad('返回 server_fee', f'{j}')
        return
    if abs(j['reward_amount'] - expected) > 0.001:
        bad('按 server_fee 计算发放额', f'期望{expected} 实际{j["reward_amount"]}')
        return
    ok(f'发放额按后端 server_fee={server_fee} 计算 → +{expected}')
    m = member_row(mid)
    if abs(m['reward_balance'] - expected) > 0.001:
        bad('余额入账', f'期望{expected} 实际{m["reward_balance"]}')
        return
    ok(f'奖励金入账 reward_balance={expected}')

    # 封顶：超长桌局（server_fee 远超 100）→ free 应封顶 100
    pid2 = add_player('封顶测试')
    mid2 = add_member(pid2, 0)
    start2 = (datetime.now() - timedelta(hours=48)).isoformat()
    sid2, spid2 = start_session(2, pid2, '封顶测试', start2)  # 换用 2 号机，1 号机仍被上一局占用
    r = grant_reward(mid2, 'free', session_id=sid2, session_player_id=spid2)
    if r.status_code != 201:
        bad('封顶发放', f'{r.status_code} {r.data}')
        return
    if r.get_json()['server_fee'] <= 100:
        bad('超长桌局台费应>100', f'{r.get_json()}')
        return
    if abs(r.get_json()['reward_amount'] - 100) > 0.001:
        bad('封顶100', f'期望100 实际{r.get_json()["reward_amount"]}')
        return
    ok('超长桌局（台费>100）→ free 封顶100')

    # 流水
    d = db()
    rows = d.execute('SELECT * FROM reward_grants WHERE member_id=?', [mid]).fetchall()
    d.close()
    if len(rows) != 1:
        bad('发放流水条数', f'期望1 实际{len(rows)}')
        return
    g1 = dict(rows[0])
    if g1['reward_type'] != 'free' or abs(g1['original_fee'] - server_fee) > 0.001 \
            or abs(g1['reward_amount'] - expected) > 0.001:
        bad('流水字段', f'{g1}')
        return
    ok('发放流水完整（类型/后端台费/金额/前后余额）')

    # 发放列表接口
    r = client.get(f'/api/rewards/grants?member_id={mid}')
    if len(r.get_json()) != 1:
        bad('grants接口', f'期望1条 实际{len(r.get_json())}')
        return
    ok('GET /api/rewards/grants 返回1条')


# ===== 验收 G：奖励金优先抵扣台费，不足走储值 =====

def test_checkout_reward_priority():
    reset_state()
    print('\n[3] 验收G：奖励金30 优先抵扣台费，不足部分走储值')
    start = (datetime.now() - timedelta(hours=2, minutes=30)).isoformat()
    fee = expected_fee(1, start)          # 真实台费（随当前时段变化，不写死）
    deduct = min(30.0, fee)               # 奖励金30，最多抵掉全部台费
    payable = round(fee - deduct, 2)
    init_balance = round(fee, 2) - 5      # 若不抵扣奖励金则余额不足，用于证明奖励金被使用

    pid = add_player('G玩家')
    mid = add_member(pid, init_balance)
    # 先开桌，再发放（grant 需要真实进行中桌局来核算 server_fee）
    sid, spid = start_session(1, pid, 'G玩家', start)
    grant_reward(mid, 'free', session_id=sid, session_player_id=spid)  # 后端按真实台费发放
    # 精确构造 reward=30 便于断言
    d = db()
    d.execute('UPDATE members SET reward_balance=30 WHERE id=?', [mid])
    d.commit()
    d.close()
    r = checkout(sid, spid, payment_method='member', member_id=mid, product_total=0)
    if r.status_code != 200:
        bad('结账成功', f'{r.status_code} {r.data}')
        return
    res = r.get_json()
    if abs(res['reward_deduct'] - deduct) > 0.001:
        bad('奖励金抵扣', f'期望{deduct} 实际{res["reward_deduct"]}')
        return
    if abs(res['grand_total'] - payable) > 0.001:
        bad('实收', f'期望{payable} 实际{res["grand_total"]}')
        return
    m = member_row(mid)
    if abs(m['reward_balance'] - (30 - deduct)) > 0.001 or abs(m['balance'] - (init_balance - payable)) > 0.001:
        bad('扣款后余额', f'奖励金{m["reward_balance"]} 储值{m["balance"]}')
        return
    ok(f'台费{fee}：奖励金-{deduct}、储值-{payable}（余额{init_balance - payable}）')

    # 核销流水
    d = db()
    rr = d.execute('SELECT * FROM reward_redemptions WHERE member_id=?', [mid]).fetchone()
    d.close()
    if not rr:
        bad('核销流水', '无记录')
        return
    rr = dict(rr)
    if abs(rr['amount'] - deduct) > 0.001 or abs(rr['table_fee'] - fee) > 0.001 \
            or abs(rr['balance_before'] - 30) > 0.001 or abs(rr['balance_after'] - (30 - deduct)) > 0.001:
        bad('核销流水字段', f'{rr}')
        return
    ok(f'核销流水完整（金额{deduct}/台费{fee}/余额30→{30 - deduct}）')

    # 核销列表接口
    r = client.get(f'/api/rewards/redemptions?member_id={mid}')
    if len(r.get_json()) != 1:
        bad('redemptions接口', f'期望1条 实际{len(r.get_json())}')
        return
    ok('GET /api/rewards/redemptions 返回1条')


# ===== 验收 H：饮料不得扣奖励金 =====

def test_checkout_product_not_rewarded():
    reset_state()
    print('\n[4] 验收H：奖励金30 + 饮料20 → 饮料不得扣奖励金')
    # 4口机1小时（台费按计费引擎实算）；奖励金30。
    # 若奖励金可抵扣商品，应扣30；正确行为是只扣台费部分。
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    fee = expected_fee(3, start)
    deduct = min(30.0, fee)          # 只抵台费，且不超过台费
    payable = round(fee - deduct + 20, 2)

    pid = add_player('H玩家')
    mid = add_member(pid, 100)
    d = db()
    d.execute('UPDATE members SET reward_balance=30 WHERE id=?', [mid])
    d.commit()
    d.close()

    sid, spid = start_session(3, pid, 'H玩家', start)  # machine 3 = 四口机
    r = checkout(sid, spid, payment_method='member', member_id=mid, product_total=20)
    if r.status_code != 200:
        bad('结账成功', f'{r.status_code} {r.data}')
        return
    res = r.get_json()
    if abs(res['reward_deduct'] - deduct) > 0.001:
        bad('奖励金抵扣', f'期望{deduct} 实际{res["reward_deduct"]}')
        return
    if abs(res['grand_total'] - payable) > 0.001:
        bad('实收', f'期望{payable} 实际{res["grand_total"]}')
        return
    m = member_row(mid)
    if abs(m['reward_balance'] - (30 - deduct)) > 0.001:
        bad('奖励金余额', f'期望{30 - deduct} 实际{m["reward_balance"]}')
        return
    ok(f'饮料20未扣奖励金：奖励金只减{deduct}（台费{fee}），剩{30 - deduct}')


# ===== 验收 I：同一订单台费+饮料，仅台费用奖励金 =====

def test_checkout_table_fee_plus_product():
    reset_state()
    print('\n[5] 验收I：台费 + 饮料20，奖励金30 → 仅台费可抵扣，商品全额')
    start = (datetime.now() - timedelta(hours=2, minutes=30)).isoformat()
    fee = expected_fee(1, start)
    deduct = min(30.0, fee)
    payable = round(fee - deduct + 20, 2)

    pid = add_player('I玩家')
    mid = add_member(pid, 100)
    d = db()
    d.execute('UPDATE members SET reward_balance=30 WHERE id=?', [mid])
    d.commit()
    d.close()

    sid, spid = start_session(1, pid, 'I玩家', start)
    r = checkout(sid, spid, payment_method='member', member_id=mid, product_total=20)
    if r.status_code != 200:
        bad('结账成功', f'{r.status_code} {r.data}')
        return
    res = r.get_json()
    if abs(res['reward_deduct'] - deduct) > 0.001:
        bad('奖励金抵扣', f'期望{deduct} 实际{res["reward_deduct"]}')
        return
    if abs(res['grand_total'] - payable) > 0.001:
        bad('实收', f'期望{payable} 实际{res["grand_total"]}')
        return
    m = member_row(mid)
    if abs(m['reward_balance'] - (30 - deduct)) > 0.001 or abs(m['balance'] - (100 - payable)) > 0.001:
        bad('扣款后余额', f'奖励金{m["reward_balance"]} 储值{m["balance"]}')
        return
    ok(f'台费{fee}-奖励金{deduct}，饮料20全额付，储值共扣{payable}')


# ===== 验收 J：历史数据不变 + 旧库迁移 =====

def test_historical_data_unchanged():
    reset_state()
    print('\n[6] 验收J：历史储值余额/流水/桌局不受影响')
    pid = add_player('J老会员')
    mid = add_member(pid, 200)
    d = db()
    d.execute("UPDATE members SET total_recharge=200 WHERE id=?", [mid])
    d.commit()
    recharge_count_before = d.execute('SELECT COUNT(*) c FROM recharge_records').fetchone()['c']
    d.close()

    # 无奖励金的普通现金结账
    start = (datetime.now() - timedelta(hours=1)).isoformat()
    sid, spid = start_session(1, pid, 'J老会员', start)
    r = checkout(sid, spid, payment_method='cash', member_id=None, product_total=0)
    if r.status_code != 200:
        bad('普通结账', f'{r.status_code} {r.data}')
        return

    d = db()
    m = dict(d.execute('SELECT * FROM members WHERE id=?', [mid]).fetchone())
    recharge_count_after = d.execute('SELECT COUNT(*) c FROM recharge_records').fetchone()['c']
    sp = dict(d.execute('SELECT * FROM session_players WHERE id=?', [spid]).fetchone())
    d.close()
    if abs(m['balance'] - 200) > 0.001:
        bad('储值余额变化', f'期望200 实际{m["balance"]}')
        return
    if recharge_count_after != recharge_count_before:
        bad('充值流水变化', f'前后{recharge_count_before}/{recharge_count_after}')
        return
    if abs(sp['reward_deduct'] - 0) > 0.001:
        bad('无奖励金玩家 reward_deduct', f'期望0 实际{sp["reward_deduct"]}')
        return
    ok('储值余额/充值流水/桌局记录均未变，reward_deduct=0')


def test_old_schema_migration():
    print('\n[7] 旧库迁移：无 reward 列的 members/session_players 平滑升级')
    # 构造一个旧 schema 库（只有旧 members 表 + 一行数据），跑 init_db 验证追加列且数据保留
    old_db_path = tempfile.NamedTemporaryFile(delete=False, suffix='.db').name
    d = sqlite3.connect(old_db_path)
    d.execute('''CREATE TABLE members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER NOT NULL,
        balance REAL DEFAULT 0,
        total_recharge REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        updated_at TEXT
    )''')
    d.execute('INSERT INTO members (player_id, balance, total_recharge, total_spent) VALUES (999, 123.45, 500, 300)')
    d.commit()
    d.close()

    import config as cfg2
    import models as models2
    old_path = cfg2.DB_PATH
    models_old_path = models2.DB_PATH  # models 直接 import 了 DB_PATH，需一并还原
    cfg2.DB_PATH = old_db_path
    models2.DB_PATH = old_db_path
    try:
        models2.init_db()
    finally:
        cfg2.DB_PATH = old_path
        models2.DB_PATH = models_old_path

    d = sqlite3.connect(old_db_path)
    d.row_factory = sqlite3.Row
    cols = [r['name'] for r in d.execute('PRAGMA table_info(members)')]
    if 'reward_balance' not in cols:
        bad('members迁移', '缺少 reward_balance 列')
        return
    row = dict(d.execute('SELECT * FROM members WHERE id=1').fetchone())
    sp_cols = [r['name'] for r in d.execute('PRAGMA table_info(session_players)')]
    tables = [r['name'] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    d.close()
    # Windows 下 init_db 打开的连接/锁可能未及时释放，防御式重试删除临时库
    import gc, time as _t
    gc.collect()
    for _ in range(10):
        try:
            os.unlink(old_db_path)
            break
        except (PermissionError, OSError):
            _t.sleep(0.2)
    if abs(row['balance'] - 123.45) > 0.001 or abs(row['reward_balance'] - 0) > 0.001:
        bad('迁移数据保留', f'{row}')
        return
    if 'reward_deduct' not in sp_cols:
        bad('session_players迁移', '缺少 reward_deduct 列')
        return
    if 'reward_grants' not in tables or 'reward_redemptions' not in tables:
        bad('流水表创建', '缺少 reward_grants/reward_redemptions')
        return
    ok('旧库升级：列追加、数据保留、流水表创建')


# ===== 附加：奖励金上下文接口（抽奖页实时台费） =====

def test_reward_context_api():
    reset_state()
    print('\n[8] GET /api/rewards/context 搜索玩家→进行中桌局实时台费')
    start = (datetime.now() - timedelta(hours=2)).isoformat()
    pid = add_player('上下文玩家')
    mid = add_member(pid, 0)
    sid, spid = start_session(1, pid, '上下文玩家', start)  # 8口机2h → 台费40

    r = client.get('/api/rewards/context?name=上下文玩家')
    if r.status_code != 200:
        bad('context接口', f'{r.status_code}')
        return
    data = r.get_json()
    if len(data) != 1:
        bad('匹配会员', f'期望1 实际{len(data)}')
        return
    ctx = data[0]
    if ctx['member_id'] != mid:
        bad('member_id', f'{ctx}')
        return
    if not ctx['active_table']:
        bad('实时台费', f'无进行中桌局 {ctx}')
        return
    # 台费取决于当前时刻所处时段（正常/自助/通宵），用计费引擎作为断言基准，
    # 避免写死 40 在跨时段运行时误报。
    at = ctx['active_table']
    d = db()
    settings = APP.get_all_settings(d)
    d.close()
    expected_fee, _ = calculate_fee(
        at['machine_type'], datetime.fromisoformat(at['start_time']), datetime.now(),
        settings, is_overnight=bool(at.get('is_overnight', 0)))
    if abs(at['current_fee'] - expected_fee) > 0.01:
        bad('实时台费', f'期望{expected_fee} 实际{at["current_fee"]}')
        return
    ok(f'搜索命中会员并返回进行中桌局实时台费{expected_fee}')

    # 无结果
    r = client.get('/api/rewards/context?name=不存在的人XYZ')
    if r.get_json() != []:
        bad('无结果', f'{r.data}')
        return
    ok('无匹配返回空列表')


if __name__ == '__main__':
    login()
    test_calculate_reward_amounts()
    test_grant_api()
    test_checkout_reward_priority()
    test_checkout_product_not_rewarded()
    test_checkout_table_fee_plus_product()
    test_historical_data_unchanged()
    test_old_schema_migration()
    test_reward_context_api()
    print(f'\n汇总: PASS={PASS}, FAIL={FAIL}')
    sys.exit(0 if FAIL == 0 else 1)
