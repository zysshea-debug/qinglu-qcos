"""真实调用链回归：普通计时缓冲阶梯（107 分钟等）端到端验证。

覆盖要求：
- 直接调用计费服务层 calculate_fee / get_billing_explanation（非孤立辅助函数）
- 通过 Flask test_client 打真实结账预览 API（/api/sessions/<id>/preview），
  验证返回的 billing_explanation 字段与计费服务层一致（actual_over_minutes /
  extra_charged_minutes / extra_fee / total），确保台桌总览 / 单人结账 /
  最终结算 同源。
- 重点复现门店实测：1 小时 47 分钟（107）应计费 40 分钟、实际超出 37 分钟、
  追加 13.333 元、合计 33 元；不得出现 50 分钟 / undefined。
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name

import models
models.DB_PATH = _tmp.name
models.init_db()
import app as APP
import billing

client = APP.app.test_client()


def login():
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin123'})
    assert r.status_code == 200, r.data


# 真实生产默认设置（init_db 种子；与门店运行设置一致）
S = {
    'billing_rounding_mode': 'buffer_step',
    'billing_buffer_minutes': 10,
    'billing_step_minutes': 10,
    'rate_8port': 20,
    'rate_4port': 10,
    'overnight_8port': 100,
    'overnight_4port': 50,
}


def _explain(minutes, machine_type='8port', is_overnight=False):
    start = datetime(2026, 10, 22, 12, 0)
    end = start + timedelta(minutes=minutes)
    fee, _ = billing.calculate_fee(machine_type, start, end, S, is_overnight=is_overnight)
    exp = billing.get_billing_explanation(machine_type, start, end, S, is_overnight=is_overnight)
    return fee, exp


def test_buffer_step_service_107_minutes():
    """门店实测案例：1 小时 47 分钟（107）。"""
    fee, exp = _explain(107)
    assert exp['full_hours'] == 1
    assert exp['extra_minutes'] == 47
    # 先扣 10 分钟免费缓冲，再阶梯：actual_over = 37
    assert exp['actual_over_minutes'] == 37, f"实际超出应为 37，实际 {exp['actual_over_minutes']}"
    # 计费时长 = ceil(37/10)*10 = 40（不得是 50）
    assert exp['extra_charged_minutes'] == 40, f"计费时长应为 40，实际 {exp['extra_charged_minutes']}"
    # 追加费用原始值 = 40/60*20 = 13.333...
    assert abs(exp['extra_fee'] - 13.333) < 0.01, f"追加应为 13.333，实际 {exp['extra_fee']}"
    # 最终金额按既有取整规则：20 + 13.333 = 33.333 -> 33
    assert exp['total'] == 33, f"合计应为 33，实际 {exp['total']}"
    assert fee == 33


def test_buffer_step_service_exact_cases():
    """要求覆盖的边界用例（精确到分钟，确定性）。"""
    cases = [
        (70,  1, 10, 0,  0,  0.0,   20),   # 1h10m 免费缓冲内，不追加
        (71,  1, 11, 1,  10, 3.33,  23),   # 1h11m 追加10分钟
        (80,  1, 20, 10, 10, 3.33,  23),   # 1h20m 追加10分钟
        (81,  1, 21, 11, 20, 6.67,  27),   # 1h21m 追加20分钟
        (107, 1, 47, 37, 40, 13.33, 33),   # 1h47m
        (110, 1, 50, 40, 40, 13.33, 33),   # 1h50m
        (153, 2, 33, 23, 30, 10.0,  50),   # 2h33m 计费30分钟，合计50
        (247, 4, 7,  0,  0,  0.0,   80),   # 4h7m 合计80
    ]
    for minutes, full, rem, actual_over, charged, extra_fee, total in cases:
        fee, exp = _explain(minutes)
        assert exp['full_hours'] == full, f"{minutes}min full={exp['full_hours']}"
        assert exp['extra_minutes'] == rem, f"{minutes}min rem={exp['extra_minutes']}"
        assert exp['actual_over_minutes'] == actual_over, f"{minutes}min actual_over={exp['actual_over_minutes']}"
        assert exp['extra_charged_minutes'] == charged, f"{minutes}min charged={exp['extra_charged_minutes']}"
        assert abs(exp['extra_fee'] - extra_fee) < 0.01, f"{minutes}min extra_fee={exp['extra_fee']}"
        assert exp['total'] == total, f"{minutes}min total={exp['total']}"
        assert fee == total


def test_no_double_count_buffer():
    """缓冲不得重复计算：actual_over 严格 = rem - buffer（当确有追加时）。"""
    for minutes in [71, 80, 81, 107, 110, 153]:
        _, exp = _explain(minutes)
        if exp['extra_charged_minutes'] > 0:
            assert exp['actual_over_minutes'] == exp['extra_minutes'] - S['billing_buffer_minutes'], \
                f"{minutes}min 实际超出应=余分-缓冲"


def test_actual_over_minutes_present_all_modes():
    """后端所有分支统一返回 actual_over_minutes（前后端字段一致，杜绝 undefined）。"""
    # 普通计时
    _, exp_normal = _explain(107)
    assert 'actual_over_minutes' in exp_normal
    # 通宵/包夜（flat）：也应包含该字段（值为 0）
    _, exp_flat = _explain(180, is_overnight=True)  # 跨 00:00-08:00 的包夜
    assert 'actual_over_minutes' in exp_flat
    assert exp_flat['actual_over_minutes'] == 0


def _create_session_107min(machine_id=1):
    start = (datetime.now() - timedelta(minutes=107)).isoformat()
    r = client.post('/api/sessions', json={
        'machine_id': machine_id,
        'start_time': start,
        'players': [{'name': '回归测试客', 'player_id': None}],
    })
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    import sqlite3
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    spid = db.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    db.close()
    return sid, spid


def test_api_preview_107_minutes_real_chain():
    """端到端：真实结账预览 API 返回 107 分钟的计费说明，与计费服务层一致。"""
    sid, spid = _create_session_107min()
    r = client.get(f'/api/sessions/{sid}/players/{spid}/preview')
    assert r.status_code == 200, r.data
    preview = r.get_json()
    exp = preview['billing_explanation']
    assert exp is not None
    # 关键：不得出现 undefined（字段必须存在且为有限数字）
    assert exp['actual_over_minutes'] is not None
    assert isinstance(exp['actual_over_minutes'], (int, float))
    assert exp['extra_charged_minutes'] is not None
    # 107 分钟：计费 40，实际超出 37，追加 13.333，合计 33
    assert exp['extra_charged_minutes'] == 40, f"API 计费时长应为 40，实际 {exp['extra_charged_minutes']}"
    assert exp['actual_over_minutes'] == 37, f"API 实际超出应为 37，实际 {exp['actual_over_minutes']}"
    assert abs(exp['extra_fee'] - 13.333) < 0.01, f"API 追加应为 13.333，实际 {exp['extra_fee']}"
    assert exp['total'] == 33, f"API 合计应为 33，实际 {exp['total']}"
    assert preview['fee'] == 33


def test_api_preview_233_minutes_real_chain():
    """端到端：2 小时 33 分钟（153）合计 50，计费 30。"""
    start = (datetime.now() - timedelta(minutes=153)).isoformat()
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': start,
        'players': [{'name': '回归测试客2', 'player_id': None}],
    })
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    import sqlite3
    db = sqlite3.connect(config.DB_PATH)
    db.row_factory = sqlite3.Row
    spid = db.execute('SELECT id FROM session_players WHERE session_id=?', [sid]).fetchone()['id']
    db.close()
    r = client.get(f'/api/sessions/{sid}/players/{spid}/preview')
    assert r.status_code == 200, r.data
    exp = r.get_json()['billing_explanation']
    assert exp['extra_charged_minutes'] == 30, f"153min 计费应为 30，实际 {exp['extra_charged_minutes']}"
    assert exp['total'] == 50, f"153min 合计应为 50，实际 {exp['total']}"
