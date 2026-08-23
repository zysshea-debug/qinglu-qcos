"""QCOS V1.6 最终修补验收（页面验收 / 临时库，不碰真实 qcos.db）

覆盖任务「提交前最后修补」的两个前端缺口：
  A) 分析中心：today_gmv(实收) / today_in_progress(进行中预计) / today_observed(经营观察值)
     验证 经营观察值 = 实收 + 进行中预计，且两个新 KPI 被后端返回、前端直接消费（不重算）。
  B) 进行中预计 = 0 时仍正常显示 ¥0，不隐藏、不崩溃。
  C) 跨午夜 active 桌：start 8/22 23:00，当前 8/23 05:00 → 实际时长 = 360 分钟（6 小时）。
  D) 已结账桌：18:40 - 23:05 → 实际时长 = 265 分钟（4 小时 25 分钟）。

实际时长一律复用 overnight.effective_end_time 统一结束时间逻辑
（actual_end_time > auto_end_at(已触发) > end_time > now），不在前端另写时间优先级。

安全：临时 SQLite + 环境变量 + PAYMENT provider=None，全程不写真实库。
"""

import os
import sys
import tempfile
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# ===== 测试专用环境变量（必须在 import config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_qcos_final_fix')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_qcos_final_fix')

import config
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
config.DB_PATH = _tmp.name
config.PAYMENT['provider'] = None

import models
models.init_db()
import app as APP
import revenue
import analytics as ANA
from models import get_db

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


def check(name, cond, msg=''):
    if cond:
        ok(name)
    else:
        bad(name, msg)


def login():
    r = client.post('/api/auth/login', json={
        'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    assert r.status_code == 200, r.data


def run():
    print('===== QCOS V1.6 最终修补验收 =====')

    # ---------------------------------------------------------------
    print('\n[1] 准备：登录 + 造一笔「今日已结账」实收（8口机单人 10h ≈ 200）')
    login()
    start = (datetime.now() - timedelta(hours=10)).isoformat()
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': start, 'players': [{'name': 'KPI甲', 'start_time': start}]})
    assert r.status_code == 201, r.data
    sid = r.get_json()['id']
    sp_id = client.get(f'/api/sessions/{sid}/preview').get_json()['players'][0]['id']
    res = client.post(f'/api/sessions/{sid}/players/{sp_id}/checkout', json={
        'payment_method': 'cash', 'member_id': None, 'product_total': 0,
        'discount_id': None, 'start_time': None, 'is_overnight': False,
        'manual_discount_type': '', 'manual_discount_value': 0})
    assert res.status_code == 200, res.data
    gmv = round(res.get_json().get('grand_total', 0), 2)
    print(f'  今日已结账实收 gmv = {gmv}')
    check('今日已结账实收 > 0', gmv > 0, f'gmv={gmv}')

    # ---------------------------------------------------------------
    print('\n[2] 场景A：分析中心 KPI（实收 / 进行中预计 / 经营观察值）')
    orig_inprog = revenue.in_progress_estimate
    revenue.in_progress_estimate = lambda db, now=None: 300.0
    try:
        biz = client.get('/api/analytics/dashboard',
                         query_string={'filter_type': 'today'}).get_json()['data']['business']
    finally:
        revenue.in_progress_estimate = orig_inprog
    check('后端返回 today_gmv', 'today_gmv' in biz, str(list(biz.keys())))
    check('后端返回 today_in_progress', 'today_in_progress' in biz, str(list(biz.keys())))
    check('后端返回 today_observed', 'today_observed' in biz, str(list(biz.keys())))
    check('今日实收 = 已结账实收', abs(biz['today_gmv'] - gmv) < 0.01,
          f"{biz['today_gmv']} vs {gmv}")
    check('进行中预计 = 300（mock active 估算）', abs(biz['today_in_progress'] - 300) < 0.01,
          str(biz['today_in_progress']))
    check('经营观察值 = 实收 + 进行中预计 (200+300=500)',
          abs(biz['today_observed'] - (gmv + 300)) < 0.01,
          f"{biz['today_observed']} vs {gmv + 300}")
    check('前端直接消费：today_observed == today_gmv + today_in_progress',
          abs(biz['today_observed'] - (biz['today_gmv'] + biz['today_in_progress'])) < 0.01)

    # ---------------------------------------------------------------
    print('\n[3] 场景B：进行中预计 = 0 仍正常显示 ¥0（不隐藏/不崩溃）')
    revenue.in_progress_estimate = lambda db, now=None: 0.0
    try:
        biz0 = client.get('/api/analytics/dashboard',
                          query_string={'filter_type': 'today'}).get_json()['data']['business']
    finally:
        revenue.in_progress_estimate = orig_inprog
    check('进行中预计 = 0', biz0['today_in_progress'] == 0, str(biz0['today_in_progress']))
    check('经营观察值 = 实收（不含虚高预计）', abs(biz0['today_observed'] - gmv) < 0.01,
          f"{biz0['today_observed']} vs {gmv}")
    check('未隐藏（字段仍为数字 0）', isinstance(biz0['today_in_progress'], (int, float)))

    # ---------------------------------------------------------------
    print('\n[4] 场景C：跨午夜 active 桌（start 8/22 23:00，当前 8/23 05:00）')
    # 造 active 通宵桌（经 API 建表后直接写时间字段，复用已有迁移列）
    r = client.post('/api/sessions', json={
        'machine_id': 1, 'start_time': '2026-08-22T23:00:00',
        'players': [{'name': '跨午夜', 'start_time': '2026-08-22T23:00:00'}]})
    assert r.status_code == 201, r.data
    sid_c = r.get_json()['id']
    db = get_db()
    db.execute(
        "UPDATE sessions SET status='active', is_overnight=1, auto_end_at='2026-08-23T07:00:00', "
        "auto_ended=0, actual_end_time=NULL WHERE id=?", [sid_c])
    db.commit()

    # 假时钟：当前 = 8/23 05:00（detail 端点内 datetime.now() 走 app.datetime）
    # 必须是真实 datetime 的子类，仅覆盖 now()，从而继承 fromisoformat 等方法
    # （detail 端点内的 _parse_dt 要用 datetime.fromisoformat，覆盖成普通类会崩）。
    class _FakeClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 23, 5, 0, 0)

    saved_dt = APP.datetime
    APP.datetime = _FakeClock
    try:
        det = client.get(f'/api/cashier_history/detail?session_id={sid_c}').get_json()
    finally:
        APP.datetime = saved_dt
    check('detail 返回 status=ok', det.get('status') == 'ok', str(det)[:200])
    s_c = det['session']
    check('实际结束时间 = 当前 05:00（effective_end_time, 未触发 auto_end）',
          s_c.get('effective_end_time') == '2026-08-23T05:00:00', str(s_c.get('effective_end_time')))
    check('实际时长 = 360 分钟（6 小时）',
          abs((s_c.get('actual_duration_minutes') or 0) - 360) < 0.01,
          str(s_c.get('actual_duration_minutes')))
    check('玩家行也带 actual_duration_minutes',
          det['players'] and abs((det['players'][0].get('actual_duration_minutes') or 0) - 360) < 0.01,
          str(det['players'][0].get('actual_duration_minutes') if det['players'] else None))

    # ---------------------------------------------------------------
    print('\n[5] 场景D：已结账桌 18:40 - 23:05 → 4 小时 25 分钟')
    # 用 machine 2，避开场景C仍在 active 的 machine 1（sid_c），否则被拒「桌面正在使用中」
    r = client.post('/api/sessions', json={
        'machine_id': 2, 'start_time': '2026-08-23T18:40:00',
        'players': [{'name': '闭桌', 'start_time': '2026-08-23T18:40:00'}]})
    assert r.status_code == 201, r.data
    sid_d = r.get_json()['id']
    db.execute(
        "UPDATE sessions SET status='closed', actual_end_time='2026-08-23T23:05:00', "
        "business_date='2026-08-23', paid_at='2026-08-23T23:05:00' WHERE id=?", [sid_d])
    db.commit()
    det_d = client.get(f'/api/cashier_history/detail?session_id={sid_d}').get_json()
    check('detail 返回 status=ok', det_d.get('status') == 'ok', str(det_d)[:200])
    s_d = det_d['session']
    check('实际结束时间 = 23:05', s_d.get('effective_end_time') == '2026-08-23T23:05:00',
          str(s_d.get('effective_end_time')))
    check('实际时长 = 265 分钟（4 小时 25 分钟）',
          abs((s_d.get('actual_duration_minutes') or 0) - 265) < 0.01,
          str(s_d.get('actual_duration_minutes')))
    check('玩家行也带 actual_duration_minutes',
          det_d['players'] and abs((det_d['players'][0].get('actual_duration_minutes') or 0) - 265) < 0.01,
          str(det_d['players'][0].get('actual_duration_minutes') if det_d['players'] else None))

    # ---------------------------------------------------------------
    print(f'\n===== 结果：PASS={PASS}  FAIL={FAIL} =====')
    if FAIL:
        raise SystemExit(1)


if __name__ == '__main__':
    run()
