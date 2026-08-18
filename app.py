"""青鹭收银系统 - Flask应用 (Phase 2)"""

from flask import Flask, render_template, request, jsonify, g, Response, session, redirect, url_for
from functools import wraps
from datetime import datetime, date, timedelta
import json
import csv
import io
import sqlite3
import re
import os

from models import get_db, init_db, get_all_settings, update_setting, hash_password
from billing import calculate_fee, calculate_discount, calculate_manual_discount, get_current_fee, get_billing_explanation
from config import (MACHINE_TYPE_LABELS, MACHINE_MAX_PLAYERS, LOTTERY_TYPES,
                    PAYMENT_METHODS, PAGE_PERMISSIONS, ROLES, PRODUCT_CATEGORIES,
                    STAFF_TYPES, STAFF_STATUS, SETTLEMENT_STATUS, SECRET_KEY,
                    CI_TIME_SLOTS, CI_PLAYER_TYPES, CI_ACTIVITY_LEVELS, CI_SPENDING_LEVELS,
                    CI_SOCIAL_INFLUENCE, CI_FREQ_LEVELS, CI_SKILL_LEVELS, CI_SCORE_DIMENSIONS,
                    CI_SCORE_WEIGHTS, CI_SCORE_DIM_LABELS, CI_MARKETING_TYPES, CI_OPERATING_STATUS)
import config
import payment as payment_module

app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # 禁用静态文件缓存
app.secret_key = SECRET_KEY

init_db()


# ===== 权限系统 =====

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.path.startswith('/api/'):
                return jsonify({'error': '请先登录'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


def role_required(page_name):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({'error': '请先登录'}), 401
                return redirect('/login')
            role = session.get('role', 'viewer')
            allowed_roles = PAGE_PERMISSIONS.get(page_name, ['admin'])
            if role not in allowed_roles:
                if request.path.startswith('/api/'):
                    return jsonify({'error': '权限不足'}), 403
                return redirect('/')
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_user(db):
    if 'user_id' not in session:
        return None
    row = db.execute('SELECT * FROM users WHERE id=?', [session['user_id']]).fetchone()
    return dict(row) if row else None


# ===== 请求钩子 =====

@app.before_request
def before_request():
    g.db = get_db()
    g.user = get_current_user(g.db)


@app.teardown_request
def teardown_request(exc):
    if hasattr(g, 'db'):
        g.db.close()


# ===== 页面路由 =====

@app.route('/')
@login_required
def index():
    return render_template('index.html', active_page='dashboard', user=g.user)


@app.route('/lottery')
@role_required('lottery')
def lottery_page():
    return render_template('lottery.html', active_page='lottery', user=g.user)


@app.route('/daily')
@role_required('daily')
def daily_page():
    return render_template('daily.html', active_page='daily', user=g.user)


@app.route('/settings')
@role_required('settings')
def settings_page():
    return render_template('settings.html', active_page='settings', user=g.user)


@app.route('/products')
@role_required('products')
def products_page():
    return render_template('products.html', active_page='products', user=g.user)


@app.route('/players')
@role_required('players')
def players_page():
    return render_template('players.html', active_page='players', user=g.user)


@app.route('/members')
@role_required('members')
def members_page():
    return render_template('members.html', active_page='members', user=g.user)


@app.route('/staff')
@role_required('staff_mgmt')
def staff_page():
    return render_template('staff.html', active_page='staff_mgmt', user=g.user,
                           staff_types=STAFF_TYPES, staff_status=STAFF_STATUS,
                           settlement_status=SETTLEMENT_STATUS)


@app.route('/competition')
@role_required('competition')
def competition_page():
    return render_template('competition.html', active_page='competition', user=g.user,
                           time_slots=CI_TIME_SLOTS, player_types=CI_PLAYER_TYPES,
                           activity_levels=CI_ACTIVITY_LEVELS, spending_levels=CI_SPENDING_LEVELS,
                           social_influence=CI_SOCIAL_INFLUENCE, freq_levels=CI_FREQ_LEVELS,
                           skill_levels=CI_SKILL_LEVELS, score_dimensions=CI_SCORE_DIMENSIONS,
                           score_weights=CI_SCORE_WEIGHTS, score_dim_labels=CI_SCORE_DIM_LABELS,
                           marketing_types=CI_MARKETING_TYPES)


@app.route('/users')
@role_required('users')
def users_page():
    return render_template('users.html', active_page='users', user=g.user)


@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect('/')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# ===== API: 认证 =====

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json
    db = g.db
    user = db.execute(
        'SELECT * FROM users WHERE username=? AND is_active=1',
        [data.get('username', '')]
    ).fetchone()
    if not user or user['password_hash'] != hash_password(data.get('password', '')):
        return jsonify({'error': '用户名或密码错误'}), 401
    session['user_id'] = user['id']
    session['username'] = user['username']
    session['role'] = user['role']
    session['name'] = user['name']
    db.execute('UPDATE users SET last_login=? WHERE id=?', [datetime.now().isoformat(), user['id']])
    db.commit()
    return jsonify({'status': 'ok', 'user': {'id': user['id'], 'username': user['username'],
                     'name': user['name'], 'role': user['role']}})


@app.route('/api/auth/me')
def api_me():
    if 'user_id' not in session:
        return jsonify({'authenticated': False})
    return jsonify({'authenticated': True, 'user': {
        'id': session['user_id'], 'username': session['username'],
        'name': session['name'], 'role': session['role']
    }})


# ===== API: 用户管理 =====

@app.route('/api/users')
@role_required('users')
def api_users():
    db = g.db
    users = db.execute('SELECT id, username, name, role, is_active, created_at, last_login FROM users ORDER BY id').fetchall()
    return jsonify([dict(u) for u in users])


@app.route('/api/users', methods=['POST'])
@role_required('users')
def api_create_user():
    data = request.json
    db = g.db
    existing = db.execute('SELECT id FROM users WHERE username=?', [data['username']]).fetchone()
    if existing:
        return jsonify({'error': '用户名已存在'}), 400
    db.execute(
        'INSERT INTO users (username, password_hash, name, role, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)',
        [data['username'], hash_password(data['password']), data['name'], data.get('role', 'staff'), datetime.now().isoformat()]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/users/<int:user_id>', methods=['PUT'])
@role_required('users')
def api_update_user(user_id):
    data = request.json
    db = g.db
    user = db.execute('SELECT * FROM users WHERE id=?', [user_id]).fetchone()
    if not user:
        return jsonify({'error': '用户不存在'}), 404
    if data.get('password'):
        db.execute('UPDATE users SET password_hash=? WHERE id=?', [hash_password(data['password']), user_id])
    if data.get('name'):
        db.execute('UPDATE users SET name=? WHERE id=?', [data['name'], user_id])
    if data.get('role'):
        db.execute('UPDATE users SET role=? WHERE id=?', [data['role'], user_id])
    if 'is_active' in data:
        db.execute('UPDATE users SET is_active=? WHERE id=?', [1 if data['is_active'] else 0, user_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@role_required('users')
def api_delete_user(user_id):
    db = g.db
    if user_id == session['user_id']:
        return jsonify({'error': '不能删除自己'}), 400
    db.execute('DELETE FROM users WHERE id=?', [user_id])
    db.commit()
    return jsonify({'status': 'ok'})


# ===== API: 台桌与会话 =====

@app.route('/api/machines')
@login_required
def api_machines():
    db = g.db
    machines = db.execute('SELECT * FROM machines ORDER BY sort_order').fetchall()
    settings = get_all_settings(db)

    result = []
    for m in machines:
        machine = dict(m)
        machine['type_label'] = MACHINE_TYPE_LABELS.get(machine['type'], machine['type'])
        machine['max_players'] = MACHINE_MAX_PLAYERS.get(machine['type'], 4)

        sess = db.execute(
            'SELECT * FROM sessions WHERE machine_id=? AND status="active"', [m['id']]
        ).fetchone()

        if sess:
            sess = dict(sess)
            machine_type = machine['type']
            players = db.execute(
                'SELECT sp.*, p.id as pid, p.is_member, p.member_id FROM session_players sp '
                'LEFT JOIN players p ON sp.player_id = p.id '
                'WHERE sp.session_id=? ORDER BY sp.id', [sess['id']]
            ).fetchall()

            player_list = []
            for p in players:
                p = dict(p)
                if p.get('start_time') and p['status'] == 'playing':
                    p_start = datetime.fromisoformat(p['start_time'])
                    p_is_overnight = bool(p.get('is_overnight', 0))
                    p_fee, p_breakdown = get_current_fee(machine_type, p_start, settings, is_overnight=p_is_overnight)
                    p['current_fee'] = p_fee
                    p['fee_breakdown'] = p_breakdown
                    p['elapsed_minutes'] = round((datetime.now() - p_start).total_seconds() / 60, 1)
                elif p.get('start_time') and p['status'] == 'checked_out':
                    p['current_fee'] = p.get('fee', 0)
                    p['elapsed_minutes'] = p.get('duration_minutes', 0)
                else:
                    p['current_fee'] = 0
                    p['elapsed_minutes'] = 0
                # 该玩家的商品
                p_products = db.execute(
                    'SELECT SUM(total) as total FROM product_sales WHERE session_player_id=?', [p['id']]
                ).fetchone()
                p['product_total'] = p_products['total'] or 0
                player_list.append(p)

            sess['players'] = player_list
            sess['active_player_count'] = sum(1 for p in player_list if p['status'] == 'playing')
            sess['checked_out_count'] = sum(1 for p in player_list if p['status'] == 'checked_out')

            start_time = datetime.fromisoformat(sess['start_time'])
            sess['elapsed_minutes'] = round((datetime.now() - start_time).total_seconds() / 60, 1)

            machine['session'] = sess
        else:
            machine['session'] = None

        result.append(machine)

    return jsonify(result)


@app.route('/api/sessions', methods=['POST'])
@role_required('dashboard')
def api_create_session():
    data = request.json
    db = g.db

    machine_id = data['machine_id']
    machine = db.execute('SELECT * FROM machines WHERE id=?', [machine_id]).fetchone()
    if not machine:
        return jsonify({'error': '台桌不存在'}), 404
    if machine['status'] == 'active':
        return jsonify({'error': '台桌正在使用中'}), 400

    now = datetime.now()
    # 支持自定义开台时间（默认当前时间）
    session_start_str = data.get('start_time')
    try:
        session_start = datetime.fromisoformat(session_start_str) if session_start_str else now
    except (ValueError, TypeError):
        session_start = now
    # 不能晚于当前时间
    if session_start > now:
        session_start = now

    cursor = db.execute(
        'INSERT INTO sessions (machine_id, start_time, status) VALUES (?, ?, "active")',
        [machine_id, session_start.isoformat()]
    )
    session_id = cursor.lastrowid

    for p in data.get('players', []):
        if p.get('name'):
            player_id = p.get('player_id')
            if not player_id:
                existing = db.execute('SELECT id FROM players WHERE name=?', [p['name']]).fetchone()
                if existing:
                    player_id = existing['id']
            # 支持每位玩家自定义开始时间和是否通宵
            player_start_str = p.get('start_time')
            try:
                player_start = datetime.fromisoformat(player_start_str) if player_start_str else session_start
            except (ValueError, TypeError):
                player_start = session_start
            if player_start > now:
                player_start = now
            is_overnight = 1 if p.get('is_overnight') else 0
            db.execute(
                'INSERT INTO session_players (session_id, player_name, player_id, is_organizer, visit_type, start_time, is_overnight, status) VALUES (?, ?, ?, ?, ?, ?, ?, "playing")',
                [session_id, p['name'], player_id, 1 if p.get('is_organizer') else 0, p.get('visit_type', 'active'), player_start.isoformat(), is_overnight]
            )

    db.execute('UPDATE machines SET status="active" WHERE id=?', [machine_id])
    db.commit()

    return jsonify({'id': session_id, 'start_time': session_start.isoformat()}), 201


@app.route('/api/sessions/<int:session_id>/preview')
@role_required('dashboard')
def api_preview_checkout(session_id):
    db = g.db
    settings = get_all_settings(db)

    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return jsonify({'error': '会话不存在'}), 404

    machine = db.execute('SELECT * FROM machines WHERE id=?', [sess['machine_id']]).fetchone()
    players = db.execute(
        'SELECT sp.*, p.id as player_id, p.is_member, p.member_id FROM session_players sp '
        'LEFT JOIN players p ON sp.player_id = p.id '
        'WHERE sp.session_id=?', [session_id]
    ).fetchall()

    start_time = datetime.fromisoformat(sess['start_time'])
    end_time = datetime.now()

    fee, breakdown = calculate_fee(machine['type'], start_time, end_time, settings)

    billing_explanation = get_billing_explanation(
        machine['type'], start_time, end_time, settings, is_overnight=True
    )

    available_discounts = []
    for p in players:
        discounts = db.execute(
            '''SELECT * FROM discounts WHERE player_name=? AND used=0
               AND lottery_date <= date('now') ORDER BY lottery_date DESC''',
            [p['player_name']]
        ).fetchall()
        for d in discounts:
            d = dict(d)
            d['type_label'] = LOTTERY_TYPES.get(d['discount_type'], {}).get('label', d['discount_type'])
            available_discounts.append(d)

    # 查商品销售
    product_sales = db.execute(
        'SELECT * FROM product_sales WHERE session_id=? ORDER BY created_at', [session_id]
    ).fetchall()
    product_total = sum(ps['total'] for ps in product_sales)

    # 查会员信息(如果桌上有会员)
    member_info = None
    for p in players:
        if p['is_member'] and p['member_id']:
            m = db.execute('SELECT * FROM members WHERE id=?', [p['member_id']]).fetchone()
            if m:
                member_info = dict(m)
                member_info['player_name'] = p['player_name']
                break

    return jsonify({
        'session_id': session_id,
        'machine_name': machine['name'],
        'machine_type': machine['type'],
        'type_label': MACHINE_TYPE_LABELS.get(machine['type'], ''),
        'start_time': sess['start_time'],
        'end_time': end_time.isoformat(),
        'duration_minutes': round((end_time - start_time).total_seconds() / 60, 1),
        'fee': fee,
        'fee_breakdown': breakdown,
        'billing_explanation': billing_explanation,
        'players': [dict(p) for p in players],
        'available_discounts': available_discounts,
        'product_sales': [dict(ps) for ps in product_sales],
        'product_total': round(product_total, 2),
        'member_info': member_info,
    })


@app.route('/api/sessions/<int:session_id>/close', methods=['POST'])
@role_required('dashboard')
def api_close_session(session_id):
    data = request.json
    db = g.db
    settings = get_all_settings(db)

    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return jsonify({'error': '会话不存在'}), 404
    if sess['status'] != 'active':
        return jsonify({'error': '会话已关闭'}), 400

    machine = db.execute('SELECT * FROM machines WHERE id=?', [sess['machine_id']]).fetchone()
    start_time = datetime.fromisoformat(sess['start_time'])
    end_time = datetime.now()

    fee, breakdown = calculate_fee(machine['type'], start_time, end_time, settings)

    discount_type = None
    discount_id = None
    discount_amount = 0

    if data.get('discount_id'):
        discount = db.execute('SELECT * FROM discounts WHERE id=?', [data['discount_id']]).fetchone()
        if discount and not discount['used']:
            discount_type = discount['discount_type']
            discount_id = discount['id']
            discount_amount = calculate_discount(fee, discount_type, discount['max_deduction'])

    final_fee = max(round(fee - discount_amount, 2), 0)

    # 商品总额
    product_total = data.get('product_total', 0)

    # 会员扣款
    member_id = data.get('member_id')
    payment_method = data.get('payment_method')
    grand_total = round(final_fee + product_total, 2)

    if payment_method == 'member' and member_id:
        member = db.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
        if not member:
            return jsonify({'error': '会员不存在'}), 400
        if member['balance'] < grand_total:
            return jsonify({'error': f'会员余额不足 (余额¥{member["balance"]:.2f}，需付¥{grand_total:.2f})'}), 400
        db.execute(
            'UPDATE members SET balance = balance - ?, total_spent = total_spent + ?, updated_at = ? WHERE id = ?',
            [grand_total, grand_total, datetime.now().isoformat(), member_id]
        )

    duration_minutes = round((end_time - start_time).total_seconds() / 60)

    db.execute(
        '''UPDATE sessions SET end_time=?, duration_minutes=?, fee=?, fee_breakdown=?,
           discount_type=?, discount_id=?, discount_amount=?, final_fee=?,
           payment_method=?, status="closed" WHERE id=?''',
        [end_time.isoformat(), duration_minutes, fee,
         json.dumps(breakdown, ensure_ascii=False),
         discount_type, discount_id, discount_amount, final_fee,
         payment_method, session_id]
    )
    db.execute('UPDATE machines SET status="idle" WHERE id=?', [sess['machine_id']])
    if discount_id:
        db.execute('UPDATE discounts SET used=1, used_session_id=? WHERE id=?', [session_id, discount_id])

    # 更新商品销售记录的支付方式和会员ID
    if product_total > 0:
        db.execute(
            'UPDATE product_sales SET payment_method=?, member_id=? WHERE session_id=? AND payment_method IS NULL',
            [payment_method, member_id, session_id]
        )

    db.commit()

    return jsonify({
        'fee': fee,
        'discount_amount': discount_amount,
        'final_fee': final_fee,
        'product_total': product_total,
        'grand_total': grand_total,
        'payment_method': payment_method,
        'member_balance_after': db.execute('SELECT balance FROM members WHERE id=?', [member_id]).fetchone()['balance'] if (payment_method == 'member' and member_id) else None
    })


# ===== API: 强制关台 =====

@app.route('/api/sessions/<int:session_id>/force-close', methods=['POST'])
@role_required('dashboard')
def api_force_close_session(session_id):
    """强制关台 - 用于异常情况"""
    db = g.db
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return jsonify({'error': '会话不存在'}), 404
    if sess['status'] != 'active':
        return jsonify({'error': '会话已关闭'}), 400

    end_time = datetime.now()

    # 把所有未结账的玩家标记为已结账（无费用）
    players = db.execute(
        'SELECT * FROM session_players WHERE session_id=? AND status="playing"', [session_id]
    ).fetchall()
    for p in players:
        p = dict(p)
        start_time_str = p.get('start_time') or sess['start_time']
        start_time = datetime.fromisoformat(start_time_str)
        duration = round((end_time - start_time).total_seconds() / 60)
        db.execute(
            'UPDATE session_players SET status="checked_out", end_time=?, duration_minutes=?, payment_method="cash" WHERE id=?',
            [end_time.isoformat(), duration, p['id']]
        )

    db.execute('UPDATE sessions SET end_time=?, status="closed" WHERE id=?', [end_time.isoformat(), session_id])
    db.execute('UPDATE machines SET status="idle" WHERE id=?', [sess['machine_id']])
    db.commit()
    return jsonify({'status': 'ok', 'message': '台桌已强制关闭'})


# ===== API: 单人结账（每人独立计时/消费/结账）=====

@app.route('/api/sessions/<int:session_id>/players', methods=['POST'])
@role_required('dashboard')
def api_add_player(session_id):
    """给活跃台桌加人"""
    data = request.json
    db = g.db
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess or sess['status'] != 'active':
        return jsonify({'error': '台桌不在使用中'}), 400
    machine = db.execute('SELECT * FROM machines WHERE id=?', [sess['machine_id']]).fetchone()
    max_players = MACHINE_MAX_PLAYERS.get(machine['type'], 4)
    current_count = db.execute(
        'SELECT COUNT(*) as cnt FROM session_players WHERE session_id=? AND status="playing"', [session_id]
    ).fetchone()['cnt']
    if current_count >= max_players:
        return jsonify({'error': f'已满员（{max_players}人）'}), 400

    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '请输入玩家姓名'}), 400

    player_id = data.get('player_id')
    if not player_id:
        existing = db.execute('SELECT id FROM players WHERE name=?', [name]).fetchone()
        if existing:
            player_id = existing['id']

    now = datetime.now()
    # 支持自定义开始时间（默认当前时间）
    start_str = data.get('start_time')
    try:
        start_time = datetime.fromisoformat(start_str) if start_str else now
    except (ValueError, TypeError):
        start_time = now
    if start_time > now:
        start_time = now
    is_overnight = 1 if data.get('is_overnight') else 0

    cursor = db.execute(
        'INSERT INTO session_players (session_id, player_name, player_id, is_organizer, visit_type, start_time, is_overnight, status) VALUES (?, ?, ?, 0, ?, ?, ?, "playing")',
        [session_id, name, player_id, data.get('visit_type', 'passive'), start_time.isoformat(), is_overnight]
    )
    db.commit()
    return jsonify({'status': 'ok', 'id': cursor.lastrowid}), 201


@app.route('/api/sessions/<int:session_id>/players/<int:sp_id>/preview', methods=['GET', 'POST'])
@role_required('dashboard')
def api_player_preview(session_id, sp_id):
    """单人结账预览"""
    db = g.db
    settings = get_all_settings(db)

    sp = db.execute(
        'SELECT sp.*, p.id as pid, p.is_member, p.member_id FROM session_players sp '
        'LEFT JOIN players p ON sp.player_id = p.id '
        'WHERE sp.id=? AND sp.session_id=?', [sp_id, session_id]
    ).fetchone()
    if not sp:
        return jsonify({'error': '玩家不存在'}), 404

    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    machine = db.execute('SELECT * FROM machines WHERE id=?', [sess['machine_id']]).fetchone()
    machine_type = machine['type']

    sp = dict(sp)
    if not sp.get('start_time'):
        # 兼容旧数据：用session的start_time
        sp['start_time'] = sess['start_time']

    # 允许前端在预览时临时覆盖开始时间和是否通宵（用于调整时间/切换计费方式实时看费用）
    data = request.get_json(silent=True) or {}
    start_time_str = data.get('start_time') or sp['start_time']
    try:
        start_time = datetime.fromisoformat(start_time_str)
    except (ValueError, TypeError):
        return jsonify({'error': '开始时间格式不正确'}), 400
    if start_time > datetime.now():
        return jsonify({'error': '开始时间不能晚于当前时间'}), 400
    is_overnight = data.get('is_overnight', sp['is_overnight'] if 'is_overnight' in sp.keys() else 0)
    if isinstance(is_overnight, str):
        is_overnight = 1 if is_overnight.lower() in ('1', 'true', 'yes') else 0
    is_overnight = bool(is_overnight)

    end_time = datetime.now()

    fee, breakdown = calculate_fee(machine_type, start_time, end_time, settings, is_overnight=is_overnight)
    sp['machine_name'] = machine['name']
    sp['machine_type'] = machine_type
    sp['type_label'] = MACHINE_TYPE_LABELS.get(machine_type, '')
    sp['start_time'] = start_time.isoformat()
    sp['end_time'] = end_time.isoformat()
    sp['duration_minutes'] = round((end_time - start_time).total_seconds() / 60, 1)
    sp['fee'] = fee
    sp['fee_breakdown'] = breakdown
    sp['is_overnight'] = 1 if is_overnight else 0

    # 手动台费折扣（金额减免 / 折扣比例）
    manual_discount_type = data.get('manual_discount_type') or sp.get('manual_discount_type') or None
    manual_discount_value = data.get('manual_discount_value')
    if manual_discount_value is None:
        manual_discount_value = sp.get('manual_discount_value') or 0
    manual_discount_amount = calculate_manual_discount(fee, manual_discount_type, manual_discount_value)
    sp['manual_discount_type'] = manual_discount_type
    sp['manual_discount_value'] = manual_discount_value
    sp['manual_discount_amount'] = manual_discount_amount
    sp['fee_after_manual'] = round(fee - manual_discount_amount, 2)

    # V1.4 计费说明（面向客人的透明计费）
    sp['billing_explanation'] = get_billing_explanation(
        machine_type, start_time, end_time, settings, is_overnight=is_overnight
    )

    # 该玩家的商品
    product_sales = db.execute(
        'SELECT * FROM product_sales WHERE session_player_id=? ORDER BY created_at', [sp_id]
    ).fetchall()
    sp['product_sales'] = [dict(ps) for ps in product_sales]
    sp['product_total'] = sum(ps['total'] for ps in product_sales)

    # 可用优惠券
    available_discounts = []
    discounts = db.execute(
        '''SELECT * FROM discounts WHERE player_name=? AND used=0
           AND lottery_date <= date('now') ORDER BY lottery_date DESC''',
        [sp['player_name']]
    ).fetchall()
    for d in discounts:
        d = dict(d)
        d['type_label'] = LOTTERY_TYPES.get(d['discount_type'], {}).get('label', d['discount_type'])
        available_discounts.append(d)
    sp['available_discounts'] = available_discounts

    # 会员信息 - 直接查 members 表，不依赖 players.is_member 标志位
    sp['member_info'] = None
    lookup_player_id = sp.get('player_id') or sp.get('pid')
    if lookup_player_id:
        m = db.execute('SELECT * FROM members WHERE player_id=?', [lookup_player_id]).fetchone()
        if m:
            sp['member_info'] = dict(m)
            sp['is_member'] = 1
            sp['member_id'] = m['id']
    if not sp['member_info']:
        # 兜底：按名字查 players 表，再查 members
        p = db.execute('SELECT id FROM players WHERE name=? ORDER BY is_member DESC LIMIT 1', [sp['player_name']])
        p = p.fetchone() if p else None
        if p:
            m = db.execute('SELECT * FROM members WHERE player_id=?', [p['id']]).fetchone()
            if m:
                sp['member_info'] = dict(m)
                sp['is_member'] = 1
                sp['member_id'] = m['id']

    return jsonify(sp)


@app.route('/api/sessions/<int:session_id>/players/<int:sp_id>/time', methods=['POST'])
@role_required('dashboard')
def api_update_player_time(session_id, sp_id):
    """持久化更新玩家在当局的开始时间和通宵标志（不结账，可随时修改）"""
    data = request.get_json(silent=True) or {}
    db = g.db

    sp = db.execute(
        'SELECT * FROM session_players WHERE id=? AND session_id=?', [sp_id, session_id]
    ).fetchone()
    if not sp:
        return jsonify({'error': '玩家不存在'}), 404
    if sp['status'] != 'playing':
        return jsonify({'error': '该玩家已结账，无法修改时间'}), 400

    # 校验并解析开始时间
    start_time_str = data.get('start_time')
    if start_time_str:
        try:
            start_time = datetime.fromisoformat(start_time_str)
        except (ValueError, TypeError):
            return jsonify({'error': '开始时间格式不正确'}), 400
        if start_time > datetime.now():
            return jsonify({'error': '开始时间不能晚于当前时间'}), 400
    else:
        start_time = datetime.fromisoformat(sp['start_time']) if sp['start_time'] else datetime.now()

    # 解析通宵标志
    is_overnight = data.get('is_overnight', sp['is_overnight'] if 'is_overnight' in sp.keys() else 0)
    if isinstance(is_overnight, str):
        is_overnight = 1 if is_overnight.lower() in ('1', 'true', 'yes') else 0
    is_overnight = bool(is_overnight)

    db.execute(
        'UPDATE session_players SET start_time=?, is_overnight=? WHERE id=?',
        [start_time.isoformat(), 1 if is_overnight else 0, sp_id]
    )
    db.commit()

    return jsonify({
        'status': 'ok',
        'start_time': start_time.isoformat(),
        'is_overnight': 1 if is_overnight else 0
    })


# ===== API: 支付确认（扫码收款必须确认到账）=====

@app.route('/api/payment/status')
@login_required
def api_payment_status():
    provider = payment_module.get_provider()
    return jsonify({
        'enabled': provider is not None,
        'provider': config.PAYMENT.get('provider'),
    })


@app.route('/api/payment/micropay', methods=['POST'])
@role_required('checkout')
def api_payment_micropay():
    provider = payment_module.get_provider()
    if not provider:
        return jsonify({'error': '未配置支付确认通道，无法确认到账'}), 400
    data = request.json or {}
    auth_code = (data.get('auth_code') or '').strip()
    method = data.get('method')
    try:
        amount = float(data.get('amount', 0))
    except (TypeError, ValueError):
        return jsonify({'error': '金额无效'}), 400
    if method not in ('scan_wechat', 'scan_alipay'):
        return jsonify({'error': '该支付方式不支持被扫确认'}), 400
    if len(auth_code) < 6:
        return jsonify({'error': '付款码无效'}), 400
    if amount <= 0:
        return jsonify({'error': '金额必须大于0'}), 400

    db = g.db
    out_trade_no = payment_module.gen_out_trade_no()
    now = datetime.now().isoformat()
    result = payment_module.micropay_with_poll(provider, auth_code, amount, out_trade_no, method)
    db.execute(
        '''INSERT INTO payments (out_trade_no, auth_code_mask, method, amount, status, transaction_id, provider, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        [out_trade_no, payment_module.mask_code(auth_code), method, amount, result.status,
         result.transaction_id, config.PAYMENT.get('provider'), now, now]
    )
    db.commit()
    return jsonify(result.to_dict())


@app.route('/api/payment/query', methods=['POST'])
@role_required('checkout')
def api_payment_query():
    provider = payment_module.get_provider()
    if not provider:
        return jsonify({'error': '未配置支付确认通道'}), 400
    data = request.json or {}
    out_trade_no = data.get('out_trade_no')
    if not out_trade_no:
        return jsonify({'error': '缺少 out_trade_no'}), 400
    db = g.db
    result = provider.query(out_trade_no)
    db.execute('UPDATE payments SET status=?, transaction_id=?, updated_at=? WHERE out_trade_no=?',
               [result.status, result.transaction_id, datetime.now().isoformat(), out_trade_no])
    db.commit()
    return jsonify(result.to_dict())


@app.route('/api/payment/notify', methods=['POST'])
def api_payment_notify():
    """支付通道异步通知（如中仑开放平台推送）。需公网可达，作为同步确认的兜底。"""
    provider = payment_module.get_provider()
    if not provider:
        return jsonify({'success': False, 'error': '未配置支付通道'}), 400
    try:
        payload = request.get_json(force=True, silent=True) or request.form.to_dict()
    except Exception:
        payload = {}
    signature = request.headers.get('sign') or request.args.get('sign') or ''
    if not provider.verify_notify(payload, signature):
        return jsonify({'success': False, 'error': '验签失败'}), 400
    out_trade_no = payload.get('out_trade_no')
    if out_trade_no:
        db = g.db
        db.execute('UPDATE payments SET status="SUCCESS", transaction_id=?, updated_at=? WHERE out_trade_no=?',
                   [payload.get('transaction_id'), datetime.now().isoformat(), out_trade_no])
        db.commit()
    return jsonify({'success': True})


@app.route('/api/sessions/<int:session_id>/players/<int:sp_id>/checkout', methods=['POST'])
@role_required('dashboard')
def api_player_checkout(session_id, sp_id):
    """单人结账"""
    data = request.json
    db = g.db
    settings = get_all_settings(db)

    sp = db.execute('SELECT * FROM session_players WHERE id=? AND session_id=?', [sp_id, session_id]).fetchone()
    if not sp:
        return jsonify({'error': '玩家不存在'}), 404
    if sp['status'] != 'playing':
        return jsonify({'error': '该玩家已结账'}), 400

    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    machine = db.execute('SELECT * FROM machines WHERE id=?', [sess['machine_id']]).fetchone()
    machine_type = machine['type']

    start_time_str = sp['start_time'] or sess['start_time']
    start_time = datetime.fromisoformat(start_time_str)

    # 结账时允许修正开始时间和是否通宵
    if data.get('start_time'):
        try:
            new_start = datetime.fromisoformat(data['start_time'])
            if new_start <= datetime.now():
                start_time = new_start
        except (ValueError, TypeError):
            pass
    is_overnight = data.get('is_overnight', sp['is_overnight'] if 'is_overnight' in sp.keys() else 0)
    if isinstance(is_overnight, str):
        is_overnight = 1 if is_overnight.lower() in ('1', 'true', 'yes') else 0
    is_overnight = bool(is_overnight)

    end_time = datetime.now()
    fee, breakdown = calculate_fee(machine_type, start_time, end_time, settings, is_overnight=is_overnight)

    # 手动台费折扣（先应用）
    manual_discount_type = data.get('manual_discount_type') or sp['manual_discount_type'] or None
    manual_discount_value = data.get('manual_discount_value')
    if manual_discount_value is None:
        manual_discount_value = sp['manual_discount_value'] or 0
    manual_discount_amount = calculate_manual_discount(fee, manual_discount_type, manual_discount_value)
    fee_after_manual = max(round(fee - manual_discount_amount, 2), 0)

    # 抽奖优惠券（在手动折扣后的台费上计算）
    discount_type = None
    discount_id = None
    discount_amount = 0

    if data.get('discount_id'):
        discount = db.execute('SELECT * FROM discounts WHERE id=?', [data['discount_id']]).fetchone()
        if discount and not discount['used']:
            discount_type = discount['discount_type']
            discount_id = discount['id']
            discount_amount = calculate_discount(fee_after_manual, discount_type, discount['max_deduction'])

    final_fee = max(round(fee_after_manual - discount_amount, 2), 0)

    # 商品总额（前端传来的购物车）
    product_total = data.get('product_total', 0)
    payment_method = data.get('payment_method')
    member_id = data.get('member_id')
    grand_total = round(final_fee + product_total, 2)

    # 支付确认门禁：扫码收款且已配置支付通道时，必须确认到账才能结账
    provider = payment_module.get_provider()
    is_scan = payment_method in ('scan_wechat', 'scan_alipay')
    if provider and is_scan:
        payment_ref = data.get('payment_ref')
        if not payment_ref:
            return jsonify({'error': '未确认支付到账，不能结账（请先扫码并完成系统确认）'}), 400
        pay = db.execute('SELECT * FROM payments WHERE out_trade_no=?', [payment_ref]).fetchone()
        if not pay or pay['status'] != 'SUCCESS':
            return jsonify({'error': '支付未确认成功，不能结账'}), 400
        if abs((pay['amount'] or 0) - grand_total) > 0.01:
            return jsonify({'error': '支付金额与结账金额不一致，不能结账'}), 400
        db.execute('UPDATE payments SET session_player_id=? WHERE out_trade_no=?', [sp_id, payment_ref])

    # 会员扣款
    if payment_method == 'member' and member_id:
        member = db.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
        if not member:
            return jsonify({'error': '会员不存在'}), 400
        if member['balance'] < grand_total:
            return jsonify({'error': f'会员余额不足 (余额¥{member["balance"]:.2f}，需付¥{grand_total:.2f})'}), 400
        db.execute(
            'UPDATE members SET balance = balance - ?, total_spent = total_spent + ?, updated_at = ? WHERE id = ?',
            [grand_total, grand_total, datetime.now().isoformat(), member_id]
        )

    duration_minutes = round((end_time - start_time).total_seconds() / 60)

    # 更新 session_player
    db.execute(
        '''UPDATE session_players SET start_time=?, is_overnight=?, end_time=?, duration_minutes=?, fee=?, fee_breakdown=?,
           manual_discount_type=?, manual_discount_value=?, manual_discount_amount=?,
           discount_type=?, discount_id=?, discount_amount=?, final_fee=?,
           product_total=?, grand_total=?, payment_method=?, status="checked_out" WHERE id=?''',
        [start_time.isoformat(), 1 if is_overnight else 0, end_time.isoformat(), duration_minutes, fee,
         json.dumps(breakdown, ensure_ascii=False),
         manual_discount_type, manual_discount_value, manual_discount_amount,
         discount_type, discount_id, discount_amount, final_fee,
         product_total, grand_total, payment_method, sp_id]
    )

    # 标记优惠券已用
    if discount_id:
        db.execute('UPDATE discounts SET used=1, used_session_id=? WHERE id=?', [session_id, discount_id])

    # 更新商品销售的支付方式和会员
    if product_total > 0:
        db.execute(
            'UPDATE product_sales SET payment_method=?, member_id=? WHERE session_player_id=? AND payment_method IS NULL',
            [payment_method, member_id, sp_id]
        )

    # 检查是否所有人都已结账
    remaining = db.execute(
        'SELECT COUNT(*) as cnt FROM session_players WHERE session_id=? AND status="playing"', [session_id]
    ).fetchone()['cnt']

    if remaining == 0:
        # 所有人都结账了，关闭台桌
        db.execute(
            'UPDATE sessions SET end_time=?, status="closed" WHERE id=?',
            [end_time.isoformat(), session_id]
        )
        db.execute('UPDATE machines SET status="idle" WHERE id=?', [sess['machine_id']])

    db.commit()

    return jsonify({
        'fee': fee,
        'manual_discount_type': manual_discount_type,
        'manual_discount_value': manual_discount_value,
        'manual_discount_amount': manual_discount_amount,
        'discount_amount': discount_amount,
        'final_fee': final_fee,
        'product_total': product_total,
        'grand_total': grand_total,
        'payment_method': payment_method,
        'session_closed': remaining == 0,
        'member_balance_after': db.execute('SELECT balance FROM members WHERE id=?', [member_id]).fetchone()['balance'] if (payment_method == 'member' and member_id) else None
    })


# ===== API: 商品 =====

@app.route('/api/products')
@role_required('products')
def api_products():
    db = g.db
    category = request.args.get('category')
    if category:
        rows = db.execute('SELECT * FROM products WHERE category=? ORDER BY sort_order, name', [category]).fetchall()
    else:
        rows = db.execute('SELECT * FROM products WHERE is_active=1 ORDER BY sort_order, name').fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['category_label'] = PRODUCT_CATEGORIES.get(r['category'], r['category'])
        result.append(r)
    return jsonify(result)


@app.route('/api/products', methods=['POST'])
@role_required('products')
def api_create_product():
    data = request.json
    db = g.db
    db.execute(
        'INSERT INTO products (name, category, price, cost, stock, is_active, sort_order, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [data['name'], data.get('category', 'drink'), data['price'], data.get('cost', 0),
         data.get('stock', -1), 1, data.get('sort_order', 0), datetime.now().isoformat(), datetime.now().isoformat()]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/products/<int:product_id>', methods=['PUT'])
@role_required('products')
def api_update_product(product_id):
    data = request.json
    db = g.db
    fields = ['name', 'category', 'price', 'cost', 'stock', 'sort_order']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE products SET {f}=?, updated_at=? WHERE id=?', [data[f], datetime.now().isoformat(), product_id])
    if 'is_active' in data:
        db.execute('UPDATE products SET is_active=?, updated_at=? WHERE id=?', [1 if data['is_active'] else 0, datetime.now().isoformat(), product_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/products/<int:product_id>', methods=['DELETE'])
@role_required('products')
def api_delete_product(product_id):
    db = g.db
    db.execute('UPDATE products SET is_active=0 WHERE id=?', [product_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/products/sell', methods=['POST'])
@role_required('products')
def api_sell_product():
    data = request.json
    db = g.db

    is_custom = data.get('is_custom', False)
    if is_custom or not data.get('product_id'):
        # 无码商品
        custom_name = (data.get('custom_name') or data.get('product_name') or '无码商品').strip()
        custom_category = (data.get('custom_category') or data.get('category') or 'other').strip()
        price = float(data.get('price', 0) or 0)
        if not custom_name or price <= 0:
            return jsonify({'error': '请输入无码商品名称和单价'}), 400
        qty = data.get('quantity', 1)
        total = round(price * qty, 2)
        session_id = data.get('session_id')
        session_player_id = data.get('session_player_id')
        member_id = data.get('member_id')

        db.execute(
            'INSERT INTO product_sales (session_id, session_player_id, product_id, product_name, category, price, quantity, total, is_custom, custom_category, payment_method, member_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [session_id, session_player_id, None, custom_name, custom_category, price, qty, total,
             1, custom_category, data.get('payment_method'), member_id, datetime.now().isoformat()]
        )
    else:
        product = db.execute('SELECT * FROM products WHERE id=?', [data['product_id']]).fetchone()
        if not product:
            return jsonify({'error': '商品不存在'}), 404
        qty = data.get('quantity', 1)
        total = round(product['price'] * qty, 2)
        session_id = data.get('session_id')
        session_player_id = data.get('session_player_id')
        member_id = data.get('member_id')

        db.execute(
            'INSERT INTO product_sales (session_id, session_player_id, product_id, product_name, category, price, quantity, total, is_custom, custom_category, payment_method, member_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [session_id, session_player_id, product['id'], product['name'], product['category'], product['price'], qty, total,
             0, None, data.get('payment_method'), member_id, datetime.now().isoformat()]
        )

        # 更新库存
        if product['stock'] >= 0:
            db.execute('UPDATE products SET stock = stock - ? WHERE id=?', [qty, product['id']])

    # 会员扣款
    if member_id:
        member = db.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
        if member:
            if member['balance'] < total:
                db.rollback()
                return jsonify({'error': '会员余额不足'}), 400
            db.execute('UPDATE members SET balance = balance - ?, total_spent = total_spent + ?, updated_at=? WHERE id=?',
                       [total, total, datetime.now().isoformat(), member_id])

    db.commit()
    return jsonify({'status': 'ok', 'total': total}), 201


@app.route('/api/products/sales')
@role_required('products')
def api_product_sales():
    db = g.db
    date_filter = request.args.get('date', date.today().isoformat())
    rows = db.execute(
        '''SELECT ps.*, s.machine_id, m.name as machine_name
           FROM product_sales ps
           LEFT JOIN sessions s ON ps.session_id = s.id
           LEFT JOIN machines m ON s.machine_id = m.id
           WHERE date(ps.created_at) = ?
           ORDER BY ps.created_at''',
        [date_filter]
    ).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['payment_label'] = PAYMENT_METHODS.get(r['payment_method'], r['payment_method'] or '')
        result.append(r)
    return jsonify(result)


# ===== API: 玩家库 =====

@app.route('/api/players')
@role_required('players')
def api_players():
    db = g.db
    search = request.args.get('search', '').strip()
    activity = request.args.get('activity', '').strip()
    ptype = request.args.get('type', '').strip()
    organizer = request.args.get('organizer', '').strip()

    query = 'SELECT * FROM players WHERE 1=1'
    params = []
    if search:
        query += ' AND (name LIKE ? OR phone LIKE ? OR wechat LIKE ? OR qcos_id LIKE ? OR real_name LIKE ?)'
        params.extend([f'%{search}%'] * 5)
    if activity:
        query += ' AND activity_level = ?'
        params.append(activity)
    if ptype:
        query += ' AND player_type = ?'
        params.append(ptype)
    if organizer:
        query += ' AND is_organizer = ?'
        params.append(1 if organizer == 'yes' else 0)
    query += ' ORDER BY total_visits DESC, name'
    rows = db.execute(query, params).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        if r['is_member'] and r['member_id']:
            m = db.execute('SELECT balance FROM members WHERE id=?', [r['member_id']]).fetchone()
            r['balance'] = m['balance'] if m else 0
        else:
            r['balance'] = None
        result.append(r)
    return jsonify(result)


@app.route('/api/players', methods=['POST'])
@role_required('players')
def api_create_player():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    cursor = db.execute(
        'INSERT INTO players (name, phone, wechat, qcos_id, dan, dan_source, first_visit, notes, is_member, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)',
        [data['name'], data.get('phone'), data.get('wechat'), data.get('qcos_id'),
         data.get('dan'), data.get('dan_source', 'self'), data.get('first_visit'),
         data.get('notes'), now, now]
    )
    db.commit()
    return jsonify({'id': cursor.lastrowid}), 201


@app.route('/api/players/<int:player_id>', methods=['PUT'])
@role_required('players')
def api_update_player(player_id):
    data = request.json
    db = g.db
    fields = ['name', 'phone', 'wechat', 'qcos_id', 'dan', 'dan_source', 'first_visit', 'notes',
              'real_name', 'preferred_name', 'gender', 'birthday', 'wechat_remark', 'area',
              'occupation', 'industry', 'source_channel', 'introducer', 'relationship_strength',
              'personality_tags', 'player_type', 'skill_level', 'preferred_mode', 'preferred_time',
              'can_overnight', 'tournament_interest', 'organizer_candidate', 'organizer_level',
              'organizer_note', 'activity_level', 'common_mode', 'active_behavior',
              'maintenance_priority', 'marketing_tags', 'risk_tags', 'follow_up_status',
              'next_follow_up', 'last_contact', 'last_contact_summary',
              'drink_preference', 'price_sensitivity', 'table_style_preference']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE players SET {f}=?, updated_at=? WHERE id=?', [data[f], datetime.now().isoformat(), player_id])
    if 'is_organizer' in data:
        db.execute('UPDATE players SET is_organizer=?, updated_at=? WHERE id=?', [1 if data['is_organizer'] else 0, datetime.now().isoformat(), player_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/players/search')
@login_required
def api_search_players():
    db = g.db
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify([])
    rows = db.execute(
        'SELECT id, name, phone, dan, is_member, member_id FROM players WHERE name LIKE ? LIMIT 10',
        [f'%{name}%']
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/players/non_members')
@login_required
def api_non_member_players():
    db = g.db
    rows = db.execute(
        "SELECT id, name, phone, dan FROM players WHERE is_member=0 OR is_member IS NULL ORDER BY total_visits DESC, name"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/players/<int:player_id>/detail')
@login_required
def api_player_detail(player_id):
    db = g.db
    p = db.execute('SELECT * FROM players WHERE id=?', [player_id]).fetchone()
    if not p:
        return jsonify({'error': '玩家不存在'}), 404
    p = dict(p)
    # 会员信息
    if p['is_member'] and p['member_id']:
        m = db.execute('SELECT * FROM members WHERE id=?', [p['member_id']]).fetchone()
        p['member_info'] = dict(m) if m else None
    else:
        p['member_info'] = None
    # 到店记录 (最近20条)
    visits = db.execute(
        'SELECT * FROM visit_records WHERE player_id=? ORDER BY visit_date DESC LIMIT 20',
        [player_id]
    ).fetchall()
    p['visit_records'] = [dict(v) for v in visits]
    # 到店统计
    stats = db.execute(
        '''SELECT
            COUNT(*) as total_visits,
            MIN(visit_date) as first_visit,
            MAX(visit_date) as last_visit,
            SUM(brought_guest) as total_brought,
            SUM(is_overnight) as total_overnight,
            SUM(CASE WHEN game_type='竞技' THEN 1 ELSE 0 END) as competitive_count,
            SUM(CASE WHEN game_type='娱乐' THEN 1 ELSE 0 END) as casual_count
           FROM visit_records WHERE player_id=?''',
        [player_id]
    ).fetchone()
    p['visit_stats'] = dict(stats) if stats else {}
    return jsonify(p)


# ===== API: 会员储值 =====

@app.route('/api/members')
@role_required('members')
def api_members():
    db = g.db
    search = request.args.get('search', '').strip()
    if search:
        rows = db.execute(
            '''SELECT m.id, m.player_id, m.phone, m.balance, m.total_recharge, m.total_spent,
                      m.status, m.created_at, m.updated_at,
                      p.name as player_name, p.phone as player_phone, p.dan
               FROM members m JOIN players p ON m.player_id = p.id
               WHERE m.status = 'active' AND (p.name LIKE ? OR m.phone LIKE ? OR p.phone LIKE ?)
               ORDER BY m.id''',
            [f'%{search}%', f'%{search}%', f'%{search}%']
        ).fetchall()
    else:
        rows = db.execute(
            '''SELECT m.id, m.player_id, m.phone, m.balance, m.total_recharge, m.total_spent,
                      m.status, m.created_at, m.updated_at,
                      p.name as player_name, p.phone as player_phone, p.dan
               FROM members m JOIN players p ON m.player_id = p.id
               WHERE m.status = 'active'
               ORDER BY m.id'''
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/members', methods=['POST'])
@role_required('members')
def api_create_member():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()

    player_id = data.get('player_id')
    new_player_name = (data.get('new_player_name') or '').strip()

    # 手机号与消费密码
    member_phone = (data.get('phone') or '').strip() or None
    pin = (data.get('pin') or '').strip()
    if pin and not re.fullmatch(r'\d{6}', pin):
        return jsonify({'error': '消费密码须为6位数字'}), 400
    pin_hash = hash_password(pin) if pin else None

    if player_id:
        player = db.execute('SELECT * FROM players WHERE id=?', [player_id]).fetchone()
        if not player:
            return jsonify({'error': '玩家不存在'}), 404
        if player['is_member']:
            return jsonify({'error': '该玩家已是会员'}), 400
        # 未填写手机号时默认继承玩家档案里的手机
        if not member_phone and player['phone']:
            member_phone = player['phone']
    elif new_player_name:
        # 创建新玩家并直接开通会员
        new_player_phone = (data.get('new_player_phone') or '').strip() or None
        cursor = db.execute(
            'INSERT INTO players (name, phone, dan, is_member, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?)',
            [new_player_name,
             new_player_phone or member_phone,
             (data.get('new_player_dan') or '').strip() or None,
             now, now]
        )
        player_id = cursor.lastrowid
        player = {'id': player_id, 'is_member': 0}
        if not member_phone:
            member_phone = new_player_phone
    else:
        return jsonify({'error': '请选择玩家或输入新玩家姓名'}), 400

    initial_balance = data.get('initial_balance', 0)
    cursor = db.execute(
        'INSERT INTO members (player_id, phone, pin_hash, balance, total_recharge, total_spent, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, "active", ?, ?)',
        [player_id, member_phone, pin_hash, initial_balance, initial_balance, now, now]
    )
    member_id = cursor.lastrowid
    db.execute('UPDATE players SET is_member=1, member_id=? WHERE id=?', [member_id, player_id])

    if initial_balance > 0:
        db.execute(
            'INSERT INTO recharge_records (member_id, amount, balance_before, balance_after, payment_method, note, operator, created_at) VALUES (?, ?, 0, ?, ?, ?, ?, ?)',
            [member_id, initial_balance, initial_balance, data.get('payment_method', 'cash'), '开户充值', session.get('name', ''), now]
        )

    db.commit()
    return jsonify({'id': member_id}), 201


@app.route('/api/members/<int:member_id>/recharge', methods=['POST'])
@role_required('members')
def api_recharge_member(member_id):
    data = request.json
    db = g.db
    member = db.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
    if not member:
        return jsonify({'error': '会员不存在'}), 404

    amount = data['amount']
    if amount <= 0:
        return jsonify({'error': '充值金额必须大于0'}), 400

    balance_before = member['balance']
    balance_after = round(balance_before + amount, 2)

    db.execute(
        'UPDATE members SET balance=?, total_recharge=total_recharge+?, updated_at=? WHERE id=?',
        [balance_after, amount, datetime.now().isoformat(), member_id]
    )
    db.execute(
        'INSERT INTO recharge_records (member_id, amount, balance_before, balance_after, payment_method, note, operator, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        [member_id, amount, balance_before, balance_after, data.get('payment_method', 'cash'),
         data.get('note', ''), session.get('name', ''), datetime.now().isoformat()]
    )
    db.commit()
    return jsonify({'balance': balance_after})


@app.route('/api/members/<int:member_id>/recharges')
@role_required('members')
def api_member_recharges(member_id):
    db = g.db
    rows = db.execute(
        'SELECT * FROM recharge_records WHERE member_id=? ORDER BY created_at DESC', [member_id]
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/members/<int:member_id>/balance')
@role_required('members')
def api_member_balance(member_id):
    db = g.db
    row = db.execute('SELECT balance FROM members WHERE id=?', [member_id]).fetchone()
    if not row:
        return jsonify({'error': '会员不存在'}), 404
    return jsonify({'balance': row['balance']})


@app.route('/api/members/<int:member_id>', methods=['PUT'])
@role_required('members')
def api_update_member(member_id):
    """修改会员资料：手机号、消费密码"""
    data = request.json
    db = g.db
    member = db.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
    if not member:
        return jsonify({'error': '会员不存在'}), 404

    updates = {}
    if 'phone' in data:
        phone = (data['phone'] or '').strip() or None
        updates['phone'] = phone

    if 'pin' in data:
        pin = (data['pin'] or '').strip()
        if pin:
            if not re.fullmatch(r'\d{6}', pin):
                return jsonify({'error': '消费密码须为6位数字'}), 400
            updates['pin_hash'] = hash_password(pin)
        else:
            updates['pin_hash'] = None

    if not updates:
        return jsonify({'error': '没有要更新的字段'}), 400

    updates['updated_at'] = datetime.now().isoformat()
    fields = ', '.join(f'{k}=?' for k in updates.keys())
    db.execute(f'UPDATE members SET {fields} WHERE id=?', list(updates.values()) + [member_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/members/<int:member_id>', methods=['DELETE'])
@role_required('members')
def api_delete_member(member_id):
    db = g.db
    member = db.execute('SELECT * FROM members WHERE id=?', [member_id]).fetchone()
    if not member:
        return jsonify({'error': '会员不存在'}), 404

    # 余额未清时给出警告，但仍允许删除（软删除），因为可能有退现金等线下处理
    balance = member['balance'] or 0
    force = request.args.get('force', '0') == '1'
    if balance > 0 and not force:
        # 返回 409 让前端二次确认
        return jsonify({
            'error': '该会员余额未清零',
            'balance': balance,
            'require_confirm': True
        }), 409

    # 软删除：保留历史充值/消费记录，仅标记会员失效并解除玩家绑定
    db.execute('UPDATE members SET status="inactive", updated_at=? WHERE id=?', [datetime.now().isoformat(), member_id])
    db.execute('UPDATE players SET is_member=0, member_id=NULL WHERE member_id=?', [member_id])
    db.commit()
    return jsonify({'status': 'ok'})


# ===== API: 抽奖管理 =====

@app.route('/api/discounts')
@role_required('lottery')
def api_discounts():
    db = g.db
    date_filter = request.args.get('date', date.today().isoformat())

    discounts = db.execute(
        'SELECT * FROM discounts WHERE lottery_date=? ORDER BY discount_type, player_name',
        [date_filter]
    ).fetchall()

    result = []
    for d in discounts:
        d = dict(d)
        d['type_label'] = LOTTERY_TYPES.get(d['discount_type'], {}).get('label', d['discount_type'])
        result.append(d)

    return jsonify(result)


@app.route('/api/discounts', methods=['POST'])
@role_required('lottery')
def api_create_discount():
    data = request.json
    db = g.db

    lt = LOTTERY_TYPES.get(data['discount_type'])
    if not lt:
        return jsonify({'error': '无效的抽奖类型'}), 400

    db.execute(
        'INSERT INTO discounts (lottery_date, player_name, discount_type, max_deduction, created_at) VALUES (?, ?, ?, ?, ?)',
        [data['lottery_date'], data['player_name'], data['discount_type'],
         data.get('max_deduction', lt['default_max']), datetime.now().isoformat()]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/discounts/<int:discount_id>', methods=['DELETE'])
@role_required('lottery')
def api_delete_discount(discount_id):
    db = g.db
    d = db.execute('SELECT * FROM discounts WHERE id=?', [discount_id]).fetchone()
    if not d:
        return jsonify({'error': '优惠券不存在'}), 404
    if d['used']:
        return jsonify({'error': '已使用，无法删除'}), 400

    db.execute('DELETE FROM discounts WHERE id=?', [discount_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/discounts/search')
@login_required
def api_search_discounts():
    db = g.db
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify([])

    discounts = db.execute(
        '''SELECT * FROM discounts WHERE player_name LIKE ? AND used=0
           AND lottery_date <= date('now') ORDER BY lottery_date DESC''',
        [f'%{name}%']
    ).fetchall()

    result = []
    for d in discounts:
        d = dict(d)
        d['type_label'] = LOTTERY_TYPES.get(d['discount_type'], {}).get('label', d['discount_type'])
        result.append(d)

    return jsonify(result)


# ===== API: 日结报表 =====

@app.route('/api/daily')
@role_required('daily')
def api_daily():
    db = g.db
    date_filter = request.args.get('date', date.today().isoformat())

    sessions = db.execute(
        '''SELECT s.*, m.name as machine_name, m.type as machine_type
           FROM sessions s JOIN machines m ON s.machine_id = m.id
           WHERE date(s.start_time) = ? AND s.status = 'closed'
           ORDER BY s.start_time''',
        [date_filter]
    ).fetchall()

    total_fee = 0
    total_discount = 0
    total_manual_discount = 0
    total_revenue = 0
    payment_breakdown = {k: 0 for k in PAYMENT_METHODS.keys()}

    session_list = []
    for s in sessions:
        s = dict(s)
        # 从 session_players 聚合费用
        players = db.execute(
            'SELECT * FROM session_players WHERE session_id=? ORDER BY id', [s['id']]
        ).fetchall()
        players = [dict(p) for p in players]

        # 兼容旧数据：如果 session_players 没有 fee 数据，用 session 级别数据
        has_player_fee = any(p.get('fee') is not None for p in players)

        if has_player_fee:
            s_fee = sum(p.get('fee', 0) or 0 for p in players)
            s_lottery_discount = sum(p.get('discount_amount', 0) or 0 for p in players)
            s_manual_discount = sum(p.get('manual_discount_amount', 0) or 0 for p in players)
            s_discount = s_lottery_discount + s_manual_discount
            s_revenue = sum(p.get('grand_total', 0) or p.get('final_fee', 0) or 0 for p in players)
            for p in players:
                pm = p.get('payment_method')
                if pm and pm in payment_breakdown:
                    payment_breakdown[pm] += p.get('grand_total', 0) or p.get('final_fee', 0) or 0
        else:
            s_fee = s.get('fee', 0) or 0
            s_discount = s.get('discount_amount', 0) or 0
            s_manual_discount = 0
            s_revenue = s.get('final_fee', 0) or 0
            pm = s.get('payment_method')
            if pm and pm in payment_breakdown:
                payment_breakdown[pm] += s_revenue

        total_fee += s_fee
        total_discount += s_discount
        total_manual_discount += s_manual_discount
        total_revenue += s_revenue

        s['fee'] = round(s_fee, 2)
        s['discount_amount'] = round(s_discount, 2)
        s['final_fee'] = round(s_revenue, 2)
        s['players_str'] = ', '.join(p['player_name'] for p in players)
        s['player_count'] = len(players)
        if has_player_fee:
            s['players_detail'] = [{
                'name': p['player_name'],
                'fee': p.get('fee', 0),
                'manual_discount': p.get('manual_discount_amount', 0),
                'discount': p.get('discount_amount', 0),
                'final_fee': p.get('final_fee', 0),
                'product_total': p.get('product_total', 0),
                'grand_total': p.get('grand_total', 0),
                'payment_method': p.get('payment_method'),
                'payment_label': PAYMENT_METHODS.get(p.get('payment_method'), ''),
                'duration': p.get('duration_minutes', 0),
                'status': p.get('status', ''),
            } for p in players]
        else:
            s['players_detail'] = []
            s['payment_label'] = PAYMENT_METHODS.get(s.get('payment_method'), '')

        # 商品总额
        ps = db.execute('SELECT SUM(total) as total FROM product_sales WHERE session_id=?', [s['id']]).fetchone()
        s['product_total'] = ps['total'] or 0

        s['type_label'] = MACHINE_TYPE_LABELS.get(s['machine_type'], s['machine_type'])
        s['payment_label'] = ', '.join(set(PAYMENT_METHODS.get(p.get('payment_method'), '') for p in players if p.get('payment_method')))
        session_list.append(s)

    active_count = db.execute(
        'SELECT COUNT(*) as cnt FROM sessions WHERE date(start_time) = ? AND status = "active"',
        [date_filter]
    ).fetchone()['cnt']

    # 商品销售统计
    product_sales = db.execute(
        'SELECT SUM(total) as total, COUNT(*) as count FROM product_sales WHERE date(created_at) = ?',
        [date_filter]
    ).fetchone()
    product_revenue = product_sales['total'] or 0
    product_count = product_sales['count'] or 0

    # 会员充值统计
    recharge_total = db.execute(
        'SELECT SUM(amount) as total FROM recharge_records WHERE date(created_at) = ?',
        [date_filter]
    ).fetchone()['total'] or 0

    # 历史组局实收（来自 Excel 导入的真实支付金额，visit_records.payment_amount）
    historical_payment = db.execute(
        'SELECT SUM(payment_amount) as total FROM visit_records WHERE visit_date = ? AND payment_amount > 0',
        [date_filter]
    ).fetchone()['total'] or 0

    return jsonify({
        'date': date_filter,
        'sessions': session_list,
        'summary': {
            'total_sessions': len(session_list),
            'active_sessions': active_count,
            'total_fee': round(total_fee, 2),
            'total_discount': round(total_discount, 2),
            'total_manual_discount': round(total_manual_discount, 2),
            'total_revenue': round(total_revenue, 2),
            'product_revenue': round(product_revenue, 2),
            'product_count': product_count,
            'recharge_total': round(recharge_total, 2),
            'historical_payment': round(historical_payment, 2),
            'grand_total': round(total_revenue + product_revenue, 2),
            'payment_breakdown': {k: round(v, 2) for k, v in payment_breakdown.items()},
        }
    })


# ===== API: 导出CSV =====

@app.route('/api/export/csv')
@role_required('daily')
def api_export_csv():
    db = g.db
    start_date = request.args.get('start_date', date.today().isoformat())
    end_date = request.args.get('end_date', (date.today() + timedelta(days=1)).isoformat())

    sessions = db.execute(
        '''SELECT s.*, m.name as machine_name, m.type as machine_type
           FROM sessions s JOIN machines m ON s.machine_id = m.id
           WHERE s.status = 'closed' AND s.start_time >= ? AND s.start_time < ?
           ORDER BY s.start_time''',
        [start_date, end_date]
    ).fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['序号', '日期', '星期', '桌号', '机型', '开始时间', '结束时间',
                     '时长(分钟)', '台费', '抵扣', '实收', '支付方式', '商品消费', '玩家', '组织者'])

    weekdays = ['一', '二', '三', '四', '五', '六', '日']

    for i, s in enumerate(sessions, 1):
        players = db.execute(
            'SELECT player_name, is_organizer FROM session_players WHERE session_id=?', [s['id']]
        ).fetchall()
        player_names = [p['player_name'] for p in players]
        organizers = [p['player_name'] for p in players if p['is_organizer']]

        start_dt = datetime.fromisoformat(s['start_time'])
        end_dt = datetime.fromisoformat(s['end_time'])

        ps = db.execute('SELECT SUM(total) as total FROM product_sales WHERE session_id=?', [s['id']]).fetchone()
        product_total = ps['total'] or 0

        writer.writerow([
            i,
            start_dt.strftime('%Y-%m-%d'),
            '周' + weekdays[start_dt.weekday()],
            s['machine_name'],
            MACHINE_TYPE_LABELS.get(s['machine_type'], s['machine_type']),
            start_dt.strftime('%H:%M'),
            end_dt.strftime('%H:%M'),
            s['duration_minutes'],
            s['fee'],
            s['discount_amount'],
            s['final_fee'],
            PAYMENT_METHODS.get(s['payment_method'], ''),
            product_total,
            ', '.join(player_names),
            ', '.join(organizers)
        ])

    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=qcos_{start_date}.csv'}
    )


# ===== API: 组局与组织者系统 =====

@app.route('/api/staff')
@role_required('staff_mgmt')
def api_staff_list():
    """场务列表"""
    db = g.db
    status_filter = request.args.get('status', '').strip()
    query = '''
        SELECT s.*, p.phone, p.dan, p.is_member, p.member_id
        FROM staff s
        LEFT JOIN players p ON s.player_id = p.id
    '''
    params = []
    if status_filter:
        query += ' WHERE s.status=?'
        params.append(status_filter)
    query += ' ORDER BY s.staff_type, s.name'
    rows = db.execute(query, params).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['type_label'] = STAFF_TYPES.get(r['staff_type'], r['staff_type'])
        r['status_label'] = STAFF_STATUS.get(r['status'], r['status'])
        result.append(r)
    return jsonify(result)


@app.route('/api/staff', methods=['POST'])
@role_required('staff_mgmt')
def api_staff_create():
    """新增场务"""
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '请输入姓名'}), 400
    # 查 player_id
    player_id = data.get('player_id')
    if not player_id:
        existing = db.execute('SELECT id FROM players WHERE name=? ORDER BY is_member DESC LIMIT 1', [name]).fetchone()
        if existing:
            player_id = existing['id']
    db.execute(
        'INSERT INTO staff (player_id, name, staff_type, commission_rate, status, joined_date, notes, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [player_id, name, data.get('staff_type', 'entertainment'),
         float(data.get('commission_rate', 0)), data.get('status', 'active'),
         data.get('joined_date', now[:10]), data.get('notes', ''), now, now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/staff/<int:staff_id>', methods=['PUT'])
@role_required('staff_mgmt')
def api_staff_update(staff_id):
    """编辑场务"""
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    fields = ['name', 'staff_type', 'commission_rate', 'status', 'joined_date', 'notes', 'player_id']
    for f in fields:
        if f in data:
            val = data[f]
            if f == 'commission_rate':
                val = float(val)
            db.execute(f'UPDATE staff SET {f}=?, updated_at=? WHERE id=?', [val, now, staff_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/staff/<int:staff_id>', methods=['DELETE'])
@role_required('staff_mgmt')
def api_staff_delete(staff_id):
    """删除场务（软删除 → 设为 inactive）"""
    db = g.db
    db.execute('UPDATE staff SET status="inactive", updated_at=? WHERE id=?', [datetime.now().isoformat(), staff_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/incentive-tiers')
@role_required('staff_mgmt')
def api_incentive_tiers():
    """激励奖金档位列表"""
    db = g.db
    rows = db.execute('SELECT * FROM incentive_tiers ORDER BY min_amount').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/incentive-tiers', methods=['POST'])
@role_required('staff_mgmt')
def api_incentive_create():
    """新增激励档位"""
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO incentive_tiers (min_amount, max_amount, bonus_amount, description, is_active, sort_order, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, 1, ?, ?, ?)',
        [float(data['min_amount']),
         float(data['max_amount']) if data.get('max_amount') else None,
         float(data['bonus_amount']),
         data.get('description', ''), data.get('sort_order', 0), now, now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/incentive-tiers/<int:tier_id>', methods=['PUT'])
@role_required('staff_mgmt')
def api_incentive_update(tier_id):
    """编辑激励档位"""
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    fields = ['min_amount', 'max_amount', 'bonus_amount', 'description', 'sort_order']
    for f in fields:
        if f in data:
            val = data[f]
            if f in ('min_amount', 'max_amount', 'bonus_amount'):
                val = float(val) if val is not None else None
            db.execute(f'UPDATE incentive_tiers SET {f}=?, updated_at=? WHERE id=?', [val, now, tier_id])
    if 'is_active' in data:
        db.execute('UPDATE incentive_tiers SET is_active=?, updated_at=? WHERE id=?',
                   [1 if data['is_active'] else 0, now, tier_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/incentive-tiers/<int:tier_id>', methods=['DELETE'])
@role_required('staff_mgmt')
def api_incentive_delete(tier_id):
    """删除激励档位"""
    db = g.db
    db.execute('DELETE FROM incentive_tiers WHERE id=?', [tier_id])
    db.commit()
    return jsonify({'status': 'ok'})


def _calc_incentive(db, total_gmv):
    """根据总金额匹配激励档位，返回 (tier_id, bonus_amount)"""
    tier = db.execute(
        'SELECT * FROM incentive_tiers WHERE is_active=1 AND ? >= min_amount '
        'AND (max_amount IS NULL OR ? < max_amount) '
        'ORDER BY min_amount DESC LIMIT 1',
        [total_gmv, total_gmv]
    ).fetchone()
    if tier:
        return tier['id'], tier['bonus_amount']
    return None, 0


@app.route('/api/staff/settlements')
@role_required('staff_mgmt')
def api_staff_settlements():
    """结算记录列表"""
    db = g.db
    staff_id = request.args.get('staff_id')
    status = request.args.get('status', '').strip()

    query = '''
        SELECT ss.*, s.name as staff_name, s.staff_type,
               it.description as incentive_desc
        FROM staff_settlements ss
        JOIN staff s ON ss.staff_id = s.id
        LEFT JOIN incentive_tiers it ON ss.incentive_tier_id = it.id
        WHERE 1=1
    '''
    params = []
    if staff_id:
        query += ' AND ss.staff_id=?'
        params.append(staff_id)
    if status:
        query += ' AND ss.status=?'
        params.append(status)
    query += ' ORDER BY ss.settlement_date DESC, ss.id DESC'
    rows = db.execute(query, params).fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['staff_type_label'] = STAFF_TYPES.get(r['staff_type'], r['staff_type'])
        r['status_label'] = SETTLEMENT_STATUS.get(r['status'], r['status'])
        result.append(r)
    return jsonify(result)


@app.route('/api/staff/settlements', methods=['POST'])
@role_required('staff_mgmt')
def api_staff_create_settlement():
    """生成结算（手动录入或自动计算）"""
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    staff_id = data['staff_id']
    staff = db.execute('SELECT * FROM staff WHERE id=?', [staff_id]).fetchone()
    if not staff:
        return jsonify({'error': '场务不存在'}), 404

    total_gmv = float(data.get('total_gmv', 0))
    commission_rate = float(data.get('commission_rate', staff['commission_rate']))
    commission_amount = round(total_gmv * commission_rate, 2)

    # 匹配激励档位
    tier_id, incentive_amount = _calc_incentive(db, total_gmv)
    # 手动覆盖激励金额
    if 'incentive_amount' in data:
        incentive_amount = float(data['incentive_amount'])
        tier_id = data.get('incentive_tier_id')

    total_payout = round(commission_amount + incentive_amount, 2)

    db.execute(
        'INSERT INTO staff_settlements '
        '(staff_id, settlement_date, period_start, period_end, total_gmv, '
        'commission_rate, commission_amount, incentive_tier_id, incentive_amount, '
        'total_payout, status, note, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [staff_id, data.get('settlement_date', now[:10]),
         data.get('period_start', now[:10]), data.get('period_end', now[:10]),
         total_gmv, commission_rate, commission_amount,
         tier_id, incentive_amount, total_payout,
         data.get('status', 'pending'), data.get('note', ''), now]
    )
    db.commit()
    return jsonify({'status': 'ok', 'total_payout': total_payout}), 201


@app.route('/api/staff/settlements/<int:settlement_id>', methods=['PUT'])
@role_required('staff_mgmt')
def api_staff_update_settlement(settlement_id):
    """更新结算状态（发放/作废）"""
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    if 'status' in data:
        paid_at = now if data['status'] == 'paid' else None
        db.execute('UPDATE staff_settlements SET status=?, paid_at=? WHERE id=?',
                   [data['status'], paid_at, settlement_id])
    if 'note' in data:
        db.execute('UPDATE staff_settlements SET note=? WHERE id=?', [data['note'], settlement_id])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/staff/dashboard')
@role_required('staff_mgmt')
def api_staff_dashboard():
    """场务看板统计"""
    db = g.db
    # 场务概览
    staff_list = db.execute('SELECT * FROM staff WHERE status="active" ORDER BY staff_type, name').fetchall()
    staff_summary = []
    for s in staff_list:
        s = dict(s)
        # 累计发放
        totals = db.execute(
            'SELECT COUNT(*) as cnt, COALESCE(SUM(total_payout), 0) as total_paid, '
            'COALESCE(SUM(commission_amount), 0) as total_commission, '
            'COALESCE(SUM(incentive_amount), 0) as total_incentive '
            'FROM staff_settlements WHERE staff_id=? AND status="paid"',
            [s['id']]
        ).fetchone()
        s['total_settlements'] = totals['cnt']
        s['total_paid'] = round(totals['total_paid'], 2)
        s['total_commission'] = round(totals['total_commission'], 2)
        s['total_incentive'] = round(totals['total_incentive'], 2)
        s['type_label'] = STAFF_TYPES.get(s['staff_type'], s['staff_type'])
        # 待发放
        pending = db.execute(
            'SELECT COUNT(*) as cnt, COALESCE(SUM(total_payout), 0) as amount '
            'FROM staff_settlements WHERE staff_id=? AND status="pending"',
            [s['id']]
        ).fetchone()
        s['pending_count'] = pending['cnt']
        s['pending_amount'] = round(pending['amount'], 2)
        staff_summary.append(s)

    # 激励档位
    tiers = db.execute('SELECT * FROM incentive_tiers WHERE is_active=1 ORDER BY min_amount').fetchall()

    # 本月汇总
    month_start = datetime.now().strftime('%Y-%m-01')
    month_stats = db.execute(
        'SELECT COUNT(*) as cnt, COALESCE(SUM(total_payout), 0) as total '
        'FROM staff_settlements WHERE settlement_date >= ?',
        [month_start]
    ).fetchone()

    return jsonify({
        'staff': staff_summary,
        'tiers': [dict(t) for t in tiers],
        'month_count': month_stats['cnt'],
        'month_total': round(month_stats['total'], 2),
    })


# ===== API: 竞争情报系统 =====

# --- M0: 元数据常量（供前端JS使用）---

@app.route('/api/ci/meta')
@role_required('competition')
def api_ci_meta():
    """返回竞争情报系统所有元数据常量（供前端JS使用）"""
    return jsonify({
        'time_slots': CI_TIME_SLOTS,
        'player_types': CI_PLAYER_TYPES,
        'activity_levels': CI_ACTIVITY_LEVELS,
        'spending_levels': CI_SPENDING_LEVELS,
        'social_influence': CI_SOCIAL_INFLUENCE,
        'freq_levels': CI_FREQ_LEVELS,
        'skill_levels': CI_SKILL_LEVELS,
        'score_dimensions': CI_SCORE_DIMENSIONS,
        'score_weights': CI_SCORE_WEIGHTS,
        'score_dim_labels': CI_SCORE_DIM_LABELS,
        'marketing_types': CI_MARKETING_TYPES,
        'operating_status': CI_OPERATING_STATUS,
    })


@app.route('/api/ci/competitors')
@role_required('competition')
def api_ci_competitors():
    db = g.db
    rows = db.execute('SELECT * FROM ci_competitors ORDER BY is_self DESC, name').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ci/competitors', methods=['POST'])
@role_required('competition')
def api_ci_competitor_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    cursor = db.execute(
        'INSERT INTO ci_competitors (name, address, open_date, area_sqm, machine_count, '
        'table_4port, table_8port, positioning, target_customers, key_selling_points, '
        'known_advantages, known_weaknesses, business_hours, operating_status, contact, notes, '
        'is_self, created_at, updated_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)',
        [data.get('name',''), data.get('address'), data.get('open_date'),
         data.get('area_sqm'), data.get('machine_count'),
         data.get('table_4port',0), data.get('table_8port',0),
         data.get('positioning'), data.get('target_customers'),
         data.get('key_selling_points'), data.get('known_advantages'),
         data.get('known_weaknesses'), data.get('business_hours'),
         data.get('operating_status', 'active'), data.get('contact'),
         data.get('notes'), now, now]
    )
    db.commit()
    return jsonify({'id': cursor.lastrowid}), 201


@app.route('/api/ci/competitors/<int:cid>', methods=['PUT'])
@role_required('competition')
def api_ci_competitor_update(cid):
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    fields = ['name','address','open_date','area_sqm','machine_count','table_4port',
              'table_8port','positioning','target_customers','key_selling_points',
              'known_advantages','known_weaknesses','business_hours','operating_status',
              'contact','notes']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE ci_competitors SET {f}=?, updated_at=? WHERE id=?', [data[f], now, cid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/competitors/<int:cid>', methods=['DELETE'])
@role_required('competition')
def api_ci_competitor_delete(cid):
    db = g.db
    comp = db.execute('SELECT * FROM ci_competitors WHERE id=?', [cid]).fetchone()
    if comp and comp['is_self']:
        return jsonify({'error': '不能删除本店记录'}), 400
    db.execute('DELETE FROM ci_competitors WHERE id=?', [cid])
    db.execute('DELETE FROM ci_pricing WHERE competitor_id=?', [cid])
    db.execute('DELETE FROM ci_traffic WHERE competitor_id=?', [cid])
    db.execute('DELETE FROM ci_player_segments WHERE competitor_id=?', [cid])
    db.execute('DELETE FROM ci_key_players WHERE competitor_id=?', [cid])
    db.execute('DELETE FROM ci_service_scores WHERE competitor_id=?', [cid])
    db.execute('DELETE FROM ci_community WHERE competitor_id=?', [cid])
    db.execute('DELETE FROM ci_marketing WHERE competitor_id=?', [cid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M2: 价格体系 ---

@app.route('/api/ci/pricing')
@role_required('competition')
def api_ci_pricing():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT p.*, c.name as competitor_name FROM ci_pricing p '
                          'JOIN ci_competitors c ON p.competitor_id=c.id WHERE p.competitor_id=? ORDER BY p.record_date DESC', [cid]).fetchall()
    else:
        rows = db.execute('SELECT p.*, c.name as competitor_name FROM ci_pricing p '
                          'JOIN ci_competitors c ON p.competitor_id=c.id ORDER BY p.record_date DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ci/pricing', methods=['POST'])
@role_required('competition')
def api_ci_pricing_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO ci_pricing (competitor_id, record_date, normal_price, night_price, '
        'overnight_price, package_price, member_price, newcustomer_offer, oldcustomer_offer, '
        'recharge_promo, tournament_fee, drink_price, notes, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        [data['competitor_id'], data.get('record_date', now[:10]),
         data.get('normal_price'), data.get('night_price'), data.get('overnight_price'),
         data.get('package_price'), data.get('member_price'), data.get('newcustomer_offer'),
         data.get('oldcustomer_offer'), data.get('recharge_promo'), data.get('tournament_fee'),
         data.get('drink_price'), data.get('notes'), now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/pricing/<int:pid>', methods=['DELETE'])
@role_required('competition')
def api_ci_pricing_delete(pid):
    db = g.db
    db.execute('DELETE FROM ci_pricing WHERE id=?', [pid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/pricing/<int:pid>', methods=['PUT'])
@role_required('competition')
def api_ci_pricing_update(pid):
    data = request.json
    db = g.db
    fields = ['competitor_id','record_date','normal_price','night_price','overnight_price',
              'package_price','member_price','newcustomer_offer','oldcustomer_offer',
              'recharge_promo','tournament_fee','drink_price','notes']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE ci_pricing SET {f}=? WHERE id=?', [data[f], pid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M3: 客流观察 ---

@app.route('/api/ci/traffic')
@role_required('competition')
def api_ci_traffic():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT t.*, c.name as competitor_name FROM ci_traffic t '
                          'JOIN ci_competitors c ON t.competitor_id=c.id WHERE t.competitor_id=? ORDER BY t.obs_date DESC, t.time_slot', [cid]).fetchall()
    else:
        rows = db.execute('SELECT t.*, c.name as competitor_name FROM ci_traffic t '
                          'JOIN ci_competitors c ON t.competitor_id=c.id ORDER BY t.obs_date DESC, t.time_slot').fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['time_slot_label'] = CI_TIME_SLOTS.get(r['time_slot'], r['time_slot'] or '')
        r['activity_label'] = CI_ACTIVITY_LEVELS.get(r['activity_level'], r['activity_level'] or '')
        result.append(r)
    return jsonify(result)


@app.route('/api/ci/traffic', methods=['POST'])
@role_required('competition')
def api_ci_traffic_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO ci_traffic (competitor_id, obs_date, time_slot, observed_tables, '
        'active_players, is_full, is_queuing, activity_level, notes, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        [data['competitor_id'], data.get('obs_date', now[:10]), data.get('time_slot'),
         data.get('observed_tables',0), data.get('active_players',0),
         1 if data.get('is_full') else 0, 1 if data.get('is_queuing') else 0,
         data.get('activity_level'), data.get('notes'), now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/traffic/<int:tid>', methods=['DELETE'])
@role_required('competition')
def api_ci_traffic_delete(tid):
    db = g.db
    db.execute('DELETE FROM ci_traffic WHERE id=?', [tid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/traffic/<int:tid>', methods=['PUT'])
@role_required('competition')
def api_ci_traffic_update(tid):
    data = request.json
    db = g.db
    fields = ['competitor_id','obs_date','time_slot','observed_tables','active_players',
              'is_full','is_queuing','activity_level','notes']
    for f in fields:
        if f in data:
            v = data[f]
            if f in ('is_full','is_queuing'):
                v = 1 if v else 0
            db.execute(f'UPDATE ci_traffic SET {f}=? WHERE id=?', [v, tid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M4: 玩家画像 ---

@app.route('/api/ci/player-segments')
@role_required('competition')
def api_ci_segments():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT s.*, c.name as competitor_name FROM ci_player_segments s '
                          'JOIN ci_competitors c ON s.competitor_id=c.id WHERE s.competitor_id=?', [cid]).fetchall()
    else:
        rows = db.execute('SELECT s.*, c.name as competitor_name FROM ci_player_segments s '
                          'JOIN ci_competitors c ON s.competitor_id=c.id').fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['player_type_label'] = CI_PLAYER_TYPES.get(r['player_type'], r['player_type'] or '')
        r['spending_label'] = CI_SPENDING_LEVELS.get(r['spending_level'], r['spending_level'] or '')
        result.append(r)
    return jsonify(result)


@app.route('/api/ci/player-segments', methods=['POST'])
@role_required('competition')
def api_ci_segment_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO ci_player_segments (competitor_id, player_type, active_time, spending_level, '
        'can_bring_guests, description, estimated_count, created_at) VALUES (?,?,?,?,?,?,?,?)',
        [data['competitor_id'], data.get('player_type'), data.get('active_time'),
         data.get('spending_level'), 1 if data.get('can_bring_guests') else 0,
         data.get('description'), data.get('estimated_count'), now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/player-segments/<int:sid>', methods=['DELETE'])
@role_required('competition')
def api_ci_segment_delete(sid):
    db = g.db
    db.execute('DELETE FROM ci_player_segments WHERE id=?', [sid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/player-segments/<int:sid>', methods=['PUT'])
@role_required('competition')
def api_ci_segment_update(sid):
    data = request.json
    db = g.db
    fields = ['competitor_id','player_type','active_time','spending_level',
              'can_bring_guests','description','estimated_count']
    for f in fields:
        if f in data:
            v = data[f]
            if f == 'can_bring_guests':
                v = 1 if v else 0
            db.execute(f'UPDATE ci_player_segments SET {f}=? WHERE id=?', [v, sid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M5: 核心玩家生态 ---

@app.route('/api/ci/key-players')
@role_required('competition')
def api_ci_key_players():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT k.*, c.name as competitor_name FROM ci_key_players k '
                          'JOIN ci_competitors c ON k.competitor_id=c.id WHERE k.competitor_id=?', [cid]).fetchall()
    else:
        rows = db.execute('SELECT k.*, c.name as competitor_name FROM ci_key_players k '
                          'JOIN ci_competitors c ON k.competitor_id=c.id').fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['freq_label'] = CI_FREQ_LEVELS.get(r['active_frequency'], r['active_frequency'] or '')
        r['skill_label'] = CI_SKILL_LEVELS.get(r['skill_level'], r['skill_level'] or '')
        r['influence_label'] = CI_SOCIAL_INFLUENCE.get(r['social_influence'], r['social_influence'] or '')
        result.append(r)
    return jsonify(result)


@app.route('/api/ci/key-players', methods=['POST'])
@role_required('competition')
def api_ci_key_player_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO ci_key_players (competitor_id, anonymous_id, active_frequency, '
        'usual_group_size, skill_level, spending_power, social_influence, conversion_value, notes, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        [data['competitor_id'], data.get('anonymous_id'), data.get('active_frequency'),
         data.get('usual_group_size'), data.get('skill_level'), data.get('spending_power'),
         data.get('social_influence'), data.get('conversion_value'), data.get('notes'), now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/key-players/<int:kid>', methods=['DELETE'])
@role_required('competition')
def api_ci_key_player_delete(kid):
    db = g.db
    db.execute('DELETE FROM ci_key_players WHERE id=?', [kid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/key-players/<int:kid>', methods=['PUT'])
@role_required('competition')
def api_ci_key_player_update(kid):
    data = request.json
    db = g.db
    fields = ['competitor_id','anonymous_id','active_frequency','usual_group_size',
              'skill_level','spending_power','social_influence','conversion_value','notes']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE ci_key_players SET {f}=? WHERE id=?', [data[f], kid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M6: 服务体验评分 ---

@app.route('/api/ci/service-scores')
@role_required('competition')
def api_ci_scores():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT s.*, c.name as competitor_name FROM ci_service_scores s '
                          'JOIN ci_competitors c ON s.competitor_id=c.id WHERE s.competitor_id=? ORDER BY s.score_date DESC', [cid]).fetchall()
    else:
        rows = db.execute('SELECT s.*, c.name as competitor_name FROM ci_service_scores s '
                          'JOIN ci_competitors c ON s.competitor_id=c.id ORDER BY s.score_date DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ci/service-scores', methods=['POST'])
@role_required('competition')
def api_ci_score_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    score_fields = [f[0] for f in CI_SCORE_DIMENSIONS]
    vals = [data['competitor_id'], data.get('score_date', now[:10])]
    for f in score_fields:
        vals.append(data.get(f))
    vals.extend([data.get('notes'), now])
    placeholders = ','.join(['?'] * (2 + len(score_fields) + 2))
    db.execute(
        f'INSERT INTO ci_service_scores (competitor_id, score_date, {", ".join(score_fields)}, notes, created_at) '
        f'VALUES ({placeholders})',
        vals
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/service-scores/<int:sid>', methods=['DELETE'])
@role_required('competition')
def api_ci_score_delete(sid):
    db = g.db
    db.execute('DELETE FROM ci_service_scores WHERE id=?', [sid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/service-scores/<int:sid>', methods=['PUT'])
@role_required('competition')
def api_ci_score_update(sid):
    data = request.json
    db = g.db
    score_fields = [f[0] for f in CI_SCORE_DIMENSIONS]
    fields = ['competitor_id','score_date'] + score_fields + ['notes']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE ci_service_scores SET {f}=? WHERE id=?', [data[f], sid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M7: 微信群生态 ---

@app.route('/api/ci/community')
@role_required('competition')
def api_ci_community():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT cm.*, c.name as competitor_name FROM ci_community cm '
                          'JOIN ci_competitors c ON cm.competitor_id=c.id WHERE cm.competitor_id=? ORDER BY cm.record_date DESC', [cid]).fetchall()
    else:
        rows = db.execute('SELECT cm.*, c.name as competitor_name FROM ci_community cm '
                          'JOIN ci_competitors c ON cm.competitor_id=c.id ORDER BY cm.record_date DESC').fetchall()
    return jsonify([dict(r) for r in rows])


@app.route('/api/ci/community', methods=['POST'])
@role_required('competition')
def api_ci_community_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO ci_community (competitor_id, record_date, group_size, active_members, '
        'daily_messages, activity_frequency, newcomer_mechanism, tournament_org, admin_activity, '
        'group_culture, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        [data['competitor_id'], data.get('record_date', now[:10]),
         data.get('group_size'), data.get('active_members'), data.get('daily_messages'),
         data.get('activity_frequency'), data.get('newcomer_mechanism'),
         data.get('tournament_org'), data.get('admin_activity'),
         data.get('group_culture'), data.get('notes'), now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/community/<int:cid>', methods=['DELETE'])
@role_required('competition')
def api_ci_community_delete(cid):
    db = g.db
    db.execute('DELETE FROM ci_community WHERE id=?', [cid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/community/<int:cid>', methods=['PUT'])
@role_required('competition')
def api_ci_community_update(cid):
    data = request.json
    db = g.db
    fields = ['competitor_id','record_date','group_size','active_members','daily_messages',
              'activity_frequency','newcomer_mechanism','tournament_org','admin_activity',
              'group_culture','notes']
    for f in fields:
        if f in data:
            db.execute(f'UPDATE ci_community SET {f}=? WHERE id=?', [data[f], cid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- M8: 营销活动 ---

@app.route('/api/ci/marketing')
@role_required('competition')
def api_ci_marketing():
    db = g.db
    cid = request.args.get('competitor_id')
    if cid:
        rows = db.execute('SELECT m.*, c.name as competitor_name FROM ci_marketing m '
                          'JOIN ci_competitors c ON m.competitor_id=c.id WHERE m.competitor_id=? ORDER BY m.activity_date DESC', [cid]).fetchall()
    else:
        rows = db.execute('SELECT m.*, c.name as competitor_name FROM ci_marketing m '
                          'JOIN ci_competitors c ON m.competitor_id=c.id ORDER BY m.activity_date DESC').fetchall()
    result = []
    for r in rows:
        r = dict(r)
        r['type_label'] = CI_MARKETING_TYPES.get(r['activity_type'], r['activity_type'] or '')
        result.append(r)
    return jsonify(result)


@app.route('/api/ci/marketing', methods=['POST'])
@role_required('competition')
def api_ci_marketing_create():
    data = request.json
    db = g.db
    now = datetime.now().isoformat()
    db.execute(
        'INSERT INTO ci_marketing (competitor_id, activity_date, activity_type, content, '
        'promotion_channel, estimated_cost, observed_effect, worth_learning, notes, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        [data['competitor_id'], data.get('activity_date', now[:10]), data.get('activity_type'),
         data.get('content'), data.get('promotion_channel'), data.get('estimated_cost', 0),
         data.get('observed_effect'), 1 if data.get('worth_learning') else 0,
         data.get('notes'), now]
    )
    db.commit()
    return jsonify({'status': 'ok'}), 201


@app.route('/api/ci/marketing/<int:mid>', methods=['DELETE'])
@role_required('competition')
def api_ci_marketing_delete(mid):
    db = g.db
    db.execute('DELETE FROM ci_marketing WHERE id=?', [mid])
    db.commit()
    return jsonify({'status': 'ok'})


@app.route('/api/ci/marketing/<int:mid>', methods=['PUT'])
@role_required('competition')
def api_ci_marketing_update(mid):
    data = request.json
    db = g.db
    fields = ['competitor_id','activity_date','activity_type','content','promotion_channel',
              'estimated_cost','observed_effect','worth_learning','notes']
    for f in fields:
        if f in data:
            v = data[f]
            if f == 'worth_learning':
                v = 1 if v else 0
            db.execute(f'UPDATE ci_marketing SET {f}=? WHERE id=?', [v, mid])
    db.commit()
    return jsonify({'status': 'ok'})


# --- 分析: 竞争评分 + SWOT + Dashboard ---

def _calc_competition_scores(db):
    """计算竞争评分 (总分100)"""
    competitors = db.execute('SELECT * FROM ci_competitors ORDER BY is_self DESC, name').fetchall()
    results = []
    for comp in competitors:
        comp = dict(comp)
        cid = comp['id']
        dim_scores = {}

        # 客流分 (20%) - 基于近30天平均活跃玩家数和上座率
        traffic = db.execute(
            'SELECT AVG(active_players) as avg_players, AVG(is_full) as avg_full, '
            'AVG(is_queuing) as avg_queue, COUNT(*) as cnt FROM ci_traffic WHERE competitor_id=? AND obs_date >= date("now","-30 days")',
            [cid]
        ).fetchone()
        if traffic and traffic['cnt'] and traffic['cnt'] > 0:
            avg_p = traffic['avg_players'] or 0
            dim_scores['traffic'] = min(10, round(avg_p / 3, 1))  # ~30人=满分
            if traffic['avg_full']:
                dim_scores['traffic'] = min(10, dim_scores['traffic'] + traffic['avg_full'] * 2)
        else:
            dim_scores['traffic'] = 0

        # 核心玩家分 (15%) - 基于核心玩家数量和社交影响力
        kp = db.execute(
            'SELECT COUNT(*) as cnt FROM ci_key_players WHERE competitor_id=?', [cid]
        ).fetchone()
        kp_count = kp['cnt'] if kp else 0
        kp_high = db.execute(
            'SELECT COUNT(*) as cnt FROM ci_key_players WHERE competitor_id=? AND social_influence="high"', [cid]
        ).fetchone()['cnt']
        dim_scores['key_players'] = min(10, kp_count * 1.5 + kp_high * 1)

        # 价格竞争力 (15%) - 有价格数据即给基础分，有优惠活动加分
        pricing = db.execute(
            'SELECT COUNT(*) as cnt FROM ci_pricing WHERE competitor_id=?', [cid]
        ).fetchone()
        if pricing and pricing['cnt'] > 0:
            latest = db.execute(
                'SELECT * FROM ci_pricing WHERE competitor_id=? ORDER BY record_date DESC LIMIT 1', [cid]
            ).fetchone()
            score = 5  # 基础分
            if latest and latest['newcustomer_offer']:
                score += 2
            if latest and latest['recharge_promo']:
                score += 2
            if latest and latest['member_price']:
                score += 1
            dim_scores['price'] = min(10, score)
        else:
            dim_scores['price'] = 0

        # 环境体验 (15%) - 服务评分中环境相关维度平均
        svc = db.execute(
            'SELECT AVG(env_score) as env, AVG(cleanliness_score) as clean, AVG(ac_air_score) as ac, '
            'AVG(seat_score) as seat FROM ci_service_scores WHERE competitor_id=? ORDER BY score_date DESC LIMIT 5',
            [cid]
        ).fetchone()
        if svc and svc['env']:
            dim_scores['environment'] = round((svc['env'] + svc['clean'] + svc['ac'] + svc['seat']) / 4, 1)
        else:
            dim_scores['environment'] = 0

        # 社群运营 (15%) - 基于微信群数据
        comm = db.execute(
            'SELECT AVG(group_size) as gsize, AVG(active_members) as amembers, '
            'AVG(daily_messages) as dmsgs, COUNT(*) as cnt FROM ci_community WHERE competitor_id=?',
            [cid]
        ).fetchone()
        if comm and comm['cnt'] and comm['cnt'] > 0:
            score = 3
            if comm['gsize']:
                score += min(3, (comm['gsize'] or 0) / 100)
            if comm['amembers']:
                score += min(2, (comm['amembers'] or 0) / 50)
            if comm['dmsgs']:
                score += min(2, (comm['dmsgs'] or 0) / 50)
            dim_scores['community'] = min(10, round(score, 1))
        else:
            dim_scores['community'] = 0

        # 服务 (10%) - 服务评分中服务相关维度平均
        svc2 = db.execute(
            'SELECT AVG(staff_attitude_score) as att, AVG(response_speed_score) as resp, '
            'AVG(newcomer_friendly_score) as newcom, AVG(regular_maintain_score) as reg '
            'FROM ci_service_scores WHERE competitor_id=? ORDER BY score_date DESC LIMIT 5',
            [cid]
        ).fetchone()
        if svc2 and svc2['att']:
            dim_scores['service'] = round((svc2['att'] + svc2['resp'] + svc2['newcom'] + svc2['reg']) / 4, 1)
        else:
            dim_scores['service'] = 0

        # 品牌影响力 (10%) - 基于已知优势和卖点
        advantages = comp.get('known_advantages', '') or ''
        selling = comp.get('key_selling_points', '') or ''
        brand_score = min(10, len(advantages.split('，')) + len(selling.split('，')))
        if comp.get('is_self'):
            brand_score = max(brand_score, 5)  # 本店保底5分
        dim_scores['brand'] = min(10, brand_score)

        # 计算加权总分
        total = 0
        for dim, weight in CI_SCORE_WEIGHTS.items():
            total += dim_scores.get(dim, 0) * weight / 100 * 10  # 1-10分 × 权重%
        comp['dim_scores'] = dim_scores
        comp['total_score'] = round(total, 1)
        results.append(comp)
    return results


@app.route('/api/ci/scores')
@role_required('competition')
def api_ci_scores_view():
    db = g.db
    results = _calc_competition_scores(db)
    return jsonify(results)


@app.route('/api/ci/swot')
@role_required('competition')
def api_ci_swot():
    """SWOT自动生成"""
    db = g.db
    scores = _calc_competition_scores(db)
    if not scores:
        return jsonify({})

    # 计算各维度均值
    dim_avgs = {}
    for dim in CI_SCORE_WEIGHTS:
        vals = [s['dim_scores'].get(dim, 0) for s in scores if s['dim_scores'].get(dim, 0) > 0]
        dim_avgs[dim] = sum(vals) / len(vals) if vals else 0

    swot = {}
    for s in scores:
        sid = str(s['id'])
        strengths = []
        weaknesses = []
        for dim in CI_SCORE_WEIGHTS:
            val = s['dim_scores'].get(dim, 0)
            if val > 0:
                label = CI_SCORE_DIM_LABELS.get(dim, dim)
                if val >= dim_avgs[dim]:
                    strengths.append(f'{label}({val}/10)')
                else:
                    weaknesses.append(f'{label}({val}/10)')
        # 机会和威胁（跨店比较）
        opportunities = []
        threats = []
        for other in scores:
            if other['id'] == s['id']:
                continue
            for dim in CI_SCORE_WEIGHTS:
                other_val = other['dim_scores'].get(dim, 0)
                my_val = s['dim_scores'].get(dim, 0)
                if other_val > my_val + 2:
                    threats.append(f"{other['name']}的{CI_SCORE_DIM_LABELS.get(dim,dim)}优势({other_val}/10)")
        # 本店优势/短板文本
        if s.get('known_advantages'):
            strengths.append(s['known_advantages'])
        if s.get('known_weaknesses'):
            weaknesses.append(s['known_weaknesses'])

        swot[sid] = {
            'name': s['name'],
            'strengths': strengths[:5],
            'weaknesses': weaknesses[:5],
            'opportunities': ['数据积累中，待分析'],
            'threats': threats[:5],
        }
    return jsonify(swot)


@app.route('/api/ci/dashboard')
@role_required('competition')
def api_ci_dashboard():
    """Dashboard 数据聚合"""
    db = g.db
    competitors = [dict(r) for r in db.execute('SELECT * FROM ci_competitors ORDER BY is_self DESC, name').fetchall()]

    # 各模块计数
    counts = {}
    for table in ['ci_pricing','ci_traffic','ci_player_segments','ci_key_players',
                   'ci_service_scores','ci_community','ci_marketing']:
        cnt = db.execute(f'SELECT COUNT(*) as c FROM {table}').fetchone()['c']
        counts[table] = cnt

    # 竞争评分
    scores = _calc_competition_scores(db)

    # 最近7天客流趋势
    traffic_trend = db.execute(
        'SELECT t.obs_date, c.name, AVG(t.active_players) as avg_players '
        'FROM ci_traffic t JOIN ci_competitors c ON t.competitor_id=c.id '
        'WHERE t.obs_date >= date("now","-7 days") '
        'GROUP BY t.obs_date, c.name ORDER BY t.obs_date'
    ).fetchall()

    # 最新服务评分
    latest_scores = []
    for comp in competitors:
        s = db.execute(
            'SELECT * FROM ci_service_scores WHERE competitor_id=? ORDER BY score_date DESC LIMIT 1',
            [comp['id']]
        ).fetchone()
        if s:
            latest_scores.append(dict(s))

    # 价格对比（最新）
    latest_pricing = []
    for comp in competitors:
        p = db.execute(
            'SELECT * FROM ci_pricing WHERE competitor_id=? ORDER BY record_date DESC LIMIT 1',
            [comp['id']]
        ).fetchone()
        if p:
            p = dict(p)
            p['competitor_name'] = comp['name']
            latest_pricing.append(p)

    return jsonify({
        'competitors': competitors,
        'counts': counts,
        'scores': scores,
        'traffic_trend': [dict(r) for r in traffic_trend],
        'latest_scores': latest_scores,
        'latest_pricing': latest_pricing,
    })


# --- CSV导出 ---

@app.route('/api/ci/export/csv')
@role_required('competition')
def api_ci_export_csv():
    db = g.db
    module = request.args.get('module', 'competitors')
    table_map = {
        'competitors': 'ci_competitors',
        'pricing': 'ci_pricing',
        'traffic': 'ci_traffic',
        'segments': 'ci_player_segments',
        'key_players': 'ci_key_players',
        'service_scores': 'ci_service_scores',
        'community': 'ci_community',
        'marketing': 'ci_marketing',
    }
    table = table_map.get(module, 'ci_competitors')
    rows = db.execute(f'SELECT * FROM {table} ORDER BY id').fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    if rows:
        cols = [c[1] for c in db.execute(f'PRAGMA table_info({table})').fetchall()]
        writer.writerow(cols)
        for r in rows:
            writer.writerow([r[c] for c in cols])

    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=ci_{module}_{date.today().isoformat()}.csv'}
    )


# --- CI CSV导入 ---

# 各模块导入列定义：(英文列名, 中文标签, 是否必填, 默认值)
CI_IMPORT_SCHEMAS = {
    'competitors': [
        ('name', '店铺名称', True, ''), ('address', '地址', False, ''),
        ('open_date', '开业时间', False, ''), ('area_sqm', '面积㎡', False, ''),
        ('machine_count', '麻将机总数', False, ''), ('table_4port', '四口桌', False, 0),
        ('table_8port', '八口桌', False, 0), ('positioning', '店铺定位', False, ''),
        ('target_customers', '主打客户群', False, ''), ('key_selling_points', '主要卖点', False, ''),
        ('known_advantages', '已知优势', False, ''), ('known_weaknesses', '已知短板', False, ''),
        ('business_hours', '营业时间', False, ''), ('operating_status', '运营状态active/preparing/closed', False, 'active'),
        ('contact', '联系方式', False, ''), ('notes', '备注', False, ''),
    ],
    'pricing': [
        ('competitor_name', '店铺名称', True, ''), ('record_date', '记录日期', False, ''),
        ('normal_price', '普通时段价格', False, ''), ('night_price', '夜场价格', False, ''),
        ('overnight_price', '通宵价格', False, ''), ('package_price', '包桌价格', False, ''),
        ('member_price', '会员价格', False, ''), ('newcustomer_offer', '新客优惠', False, ''),
        ('oldcustomer_offer', '老客优惠', False, ''), ('recharge_promo', '充值活动', False, ''),
        ('tournament_fee', '比赛收费', False, ''), ('drink_price', '饮品收费', False, ''),
        ('notes', '备注', False, ''),
    ],
    'traffic': [
        ('competitor_name', '店铺名称', True, ''), ('obs_date', '观察日期', False, ''),
        ('time_slot', '时间段afternoon/evening/night/late_night', False, ''),
        ('observed_tables', '观察桌数', False, 0), ('active_players', '活跃玩家数量', False, 0),
        ('is_full', '是否满桌0/1', False, 0), ('is_queuing', '是否排队0/1', False, 0),
        ('activity_level', '活跃程度high/medium/low/empty', False, ''), ('notes', '备注', False, ''),
    ],
    'segments': [
        ('competitor_name', '店铺名称', True, ''), ('player_type', '玩家类型', False, ''),
        ('active_time', '活跃时间', False, ''), ('spending_level', '消费能力high/medium/low', False, ''),
        ('can_bring_guests', '带人能力0/1', False, 0), ('estimated_count', '估计人数', False, ''),
        ('description', '特征描述', False, ''),
    ],
    'key_players': [
        ('competitor_name', '店铺名称', True, ''), ('anonymous_id', '匿名编号', False, ''),
        ('active_frequency', '活跃频率daily/weekly/biweekly/monthly/rare', False, ''),
        ('usual_group_size', '常带人数', False, 0), ('skill_level', '技术水平expert/intermediate/beginner', False, ''),
        ('spending_power', '消费能力', False, ''), ('social_influence', '社交影响力high/medium/low', False, ''),
        ('conversion_value', '转化价值', False, ''), ('notes', '备注', False, ''),
    ],
    'service_scores': [
        ('competitor_name', '店铺名称', True, ''), ('score_date', '评分日期', False, ''),
        ('env_score', '环境', False, None), ('cleanliness_score', '卫生', False, None),
        ('ac_air_score', '空调空气', False, None), ('seat_score', '座椅舒适度', False, None),
        ('staff_attitude_score', '店员态度', False, None), ('response_speed_score', '回复速度', False, None),
        ('newcomer_friendly_score', '新人友好度', False, None), ('regular_maintain_score', '老客维护', False, None),
        ('community_atmosphere_score', '社群氛围', False, None), ('overall_score', '整体体验', False, None),
        ('notes', '备注', False, ''),
    ],
    'community': [
        ('competitor_name', '店铺名称', True, ''), ('record_date', '记录日期', False, ''),
        ('group_size', '群规模', False, ''), ('active_members', '活跃人数', False, ''),
        ('daily_messages', '每日消息数量', False, ''), ('activity_frequency', '活动频率', False, ''),
        ('newcomer_mechanism', '新人欢迎机制', False, ''), ('tournament_org', '比赛组织', False, ''),
        ('admin_activity', '管理员活跃度', False, ''), ('group_culture', '群文化特点', False, ''),
        ('notes', '备注', False, ''),
    ],
    'marketing': [
        ('competitor_name', '店铺名称', True, ''), ('activity_date', '活动日期', False, ''),
        ('activity_type', '活动类型tournament/discount/recharge/newcomer/social/other', False, ''),
        ('content', '活动内容', False, ''), ('promotion_channel', '推广方式', False, ''),
        ('estimated_cost', '预计成本', False, 0), ('observed_effect', '效果观察', False, ''),
        ('worth_learning', '值得学习0/1', False, 0), ('notes', '备注', False, ''),
    ],
}


def _ci_module_table(module):
    return {
        'competitors': 'ci_competitors', 'pricing': 'ci_pricing', 'traffic': 'ci_traffic',
        'segments': 'ci_player_segments', 'key_players': 'ci_key_players',
        'service_scores': 'ci_service_scores', 'community': 'ci_community', 'marketing': 'ci_marketing',
    }.get(module)


@app.route('/api/ci/import/template')
@role_required('competition')
def api_ci_import_template():
    """下载CSV导入模板（表头=中文标签+示例行）"""
    module = request.args.get('module', 'competitors')
    schema = CI_IMPORT_SCHEMAS.get(module)
    if not schema:
        return jsonify({'error': '未知模块'}), 400
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for _, label, _, _ in schema])
    # 示例行（用第一家店的名称）
    db = g.db
    first = db.execute('SELECT name FROM ci_competitors WHERE is_self=0 ORDER BY id LIMIT 1').fetchone()
    example = []
    for col, label, required, default in schema:
        if col == 'competitor_name':
            example.append(first['name'] if first else '朵拉')
        elif default != '' and default is not None:
            example.append(default if not isinstance(default, bool) else (1 if default else 0))
        elif col in ('record_date', 'score_date', 'activity_date', 'obs_date'):
            example.append(date.today().isoformat())
        else:
            example.append('')
    writer.writerow(example)
    return Response(
        '\ufeff' + output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=ci_{module}_template.csv'}
    )


@app.route('/api/ci/import/csv', methods=['POST'])
@role_required('competition')
def api_ci_import_csv():
    """导入CSV数据。请求体: {"module": "pricing", "rows": [{列名: 值}, ...]}
    列名支持中文标签或英文列名；店铺名称自动转换为competitor_id。"""
    data = request.json or {}
    module = data.get('module', '')
    rows = data.get('rows', [])
    schema = CI_IMPORT_SCHEMAS.get(module)
    table = _ci_module_table(module)
    if not schema or not table:
        return jsonify({'error': '未知模块'}), 400
    if not rows:
        return jsonify({'error': '没有可导入的数据'}), 400

    # 构建列名映射：中文标签 -> 英文列名
    label2col = {label: col for col, label, _, _ in schema}
    db = g.db
    # 店铺名 -> id 映射
    comp_map = {r['name']: r['id'] for r in db.execute('SELECT id, name FROM ci_competitors').fetchall()}

    now = datetime.now().isoformat()
    imported = 0
    errors = []
    for i, raw in enumerate(rows, start=1):
        try:
            record = {}
            for k, v in raw.items():
                col = label2col.get(k, k)  # 中文标签转英文列名
                if col in schema_cols(schema):
                    record[col] = v
            # 店铺名称 -> competitor_id
            if 'competitor_name' in record:
                cname = str(record.pop('competitor_name') or '').strip()
                cid = comp_map.get(cname)
                if not cid:
                    # 尝试精确匹配失败则跳过该行
                    errors.append(f'第{i}行: 店铺「{cname}」不存在，已跳过')
                    continue
                record['competitor_id'] = cid
            elif module != 'competitors' and not record.get('competitor_id'):
                errors.append(f'第{i}行: 缺少店铺名称')
                continue

            # 类型转换
            type_map = {}
            for col, label, required, default in schema:
                if col in ('is_full', 'is_queuing', 'worth_learning', 'can_bring_guests'):
                    type_map[col] = 'bool'
                elif col in ('area_sqm', 'machine_count', 'table_4port', 'table_8port',
                             'observed_tables', 'active_players', 'estimated_count',
                             'usual_group_size', 'group_size', 'active_members',
                             'daily_messages', 'estimated_cost'):
                    type_map[col] = 'num'
                elif col in ('env_score', 'cleanliness_score', 'ac_air_score', 'seat_score',
                             'staff_attitude_score', 'response_speed_score',
                             'newcomer_friendly_score', 'regular_maintain_score',
                             'community_atmosphere_score', 'overall_score'):
                    type_map[col] = 'score'
            for col, t in type_map.items():
                if col in record and record[col] not in (None, ''):
                    v = record[col]
                    try:
                        if t == 'bool':
                            record[col] = 1 if str(v).strip() in ('1', 'true', 'True', '是', 'YES', 'yes') else 0
                        elif t == 'num':
                            record[col] = float(v) if t == 'num' and col == 'area_sqm' else int(float(v))
                        elif t == 'score':
                            record[col] = int(float(v))
                    except (ValueError, TypeError):
                        record[col] = None

            # 组装INSERT
            cols = [c for c in record.keys() if c != 'competitor_name']
            if not cols:
                errors.append(f'第{i}行: 无有效字段')
                continue
            cols.append('created_at')
            placeholders = ','.join(['?'] * len(cols))
            vals = [record.get(c) for c in cols[:-1]] + [now]
            db.execute(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({placeholders})', vals)
            imported += 1
        except Exception as e:
            errors.append(f'第{i}行: {e}')
    db.commit()
    return jsonify({'imported': imported, 'errors': errors})


def schema_cols(schema):
    return {col for col, _, _, _ in schema} | {'competitor_id'}


# ===== API: 设置 =====

@app.route('/api/settings')
@role_required('settings')
def api_get_settings():
    return jsonify(get_all_settings(g.db))


@app.route('/api/settings', methods=['POST'])
@role_required('settings')
def api_update_settings():
    data = request.json
    db = g.db
    for key, value in data.items():
        update_setting(db, key, value)
    return jsonify({'status': 'ok'})


@app.route('/api/health')
def api_health():
    return jsonify({'status': 'ok', 'time': datetime.now().isoformat()})


# ===== 运营大脑 (V1.1 Operation Intelligence Layer) =====
from operations import (compute_player_scores, generate_operation_tasks,
                        get_operations_dashboard, get_staff_dashboard,
                        complete_task, get_gmv_summary, LEVEL_LABELS, TASK_TYPE_LABELS)

# ===== V1.2 玩家关系网络 + 智能组局匹配 =====
from player_matching import (compute_initiative, compute_table_style,
                             analyze_relationships, get_relationships,
                             get_relationship_suggestions, save_relationship,
                             match_table, generate_table_match_tasks,
                             INITIATIVE_LEVELS, INITIATIVE_TIERS,
                             TABLE_STYLES, REL_TYPES)


@app.route('/operations')
@role_required('operations')
def operations_page():
    return render_template('operations.html', active_page='operations', user=g.user)


@app.route('/staff_tasks')
@role_required('staff_tasks')
def staff_tasks_page():
    return render_template('staff_tasks.html', active_page='staff_tasks', user=g.user)


@app.route('/api/operations/score', methods=['POST'])
@role_required('operations')
def api_op_score():
    results = compute_player_scores(g.db)
    return jsonify({'status': 'ok', 'count': len(results), 'results': results})


@app.route('/api/operations/generate-tasks', methods=['POST'])
@role_required('staff_tasks')
def api_op_generate_tasks():
    summary = generate_operation_tasks(g.db)
    return jsonify({'status': 'ok', 'summary': summary})


@app.route('/api/operations/dashboard')
@role_required('operations')
def api_op_dashboard():
    return jsonify(get_operations_dashboard(g.db))


@app.route('/api/operations/staff')
@role_required('staff_tasks')
def api_op_staff():
    return jsonify(get_staff_dashboard(g.db))


@app.route('/api/operations/tasks/<int:task_id>/complete', methods=['POST'])
@role_required('staff_tasks')
def api_op_complete_task(task_id):
    operator = g.user['name'] if g.user else 'unknown'
    ok = complete_task(g.db, task_id, operator)
    return jsonify({'status': 'ok' if ok else 'not_found'})


# =====================================================================
# V1.2 玩家关系网络 + 智能组局匹配
# =====================================================================

@app.route('/table_matcher')
@role_required('table_matcher')
def table_matcher_page():
    return render_template('table_matcher.html', active_page='table_matcher', user=g.user)


@app.route('/api/matching/recompute', methods=['POST'])
@role_required('table_matcher')
def api_matching_recompute():
    ini = compute_initiative(g.db)
    sty = compute_table_style(g.db)
    return jsonify({'status': 'ok', 'initiative_count': len(ini), 'style_count': len(sty)})


@app.route('/api/table_matcher/match', methods=['POST'])
@role_required('table_matcher')
def api_table_matcher_match():
    data = request.json or {}
    table_style = data.get('table_style', 'competitive')
    existing_ids = data.get('existing_ids', [])
    missing_count = int(data.get('missing_count', 0) or 0)
    stake = data.get('stake')
    if table_style not in ('competitive', 'entertainment'):
        return jsonify({'error': '局型参数错误'}), 400
    result = match_table(g.db, table_style, existing_ids, missing_count, stake)
    return jsonify(result)


@app.route('/api/table_matcher/generate-tasks', methods=['POST'])
@role_required('table_matcher')
def api_table_matcher_tasks():
    data = request.json or {}
    table_style = data.get('table_style', 'competitive')
    existing_ids = data.get('existing_ids', [])
    missing_count = int(data.get('missing_count', 0) or 0)
    operator = g.user['name'] if g.user else 'unknown'
    summary = generate_table_match_tasks(g.db, table_style, existing_ids, missing_count, operator)
    return jsonify({'status': 'ok', 'summary': summary})


@app.route('/api/players/<int:player_id>/relationships')
@role_required('players')
def api_player_relationships(player_id):
    db = g.db
    p = db.execute('SELECT id, name FROM players WHERE id=?', [player_id]).fetchone()
    if not p:
        return jsonify({'error': '玩家不存在'}), 404
    manual = get_relationships(db, player_id)
    suggestions = get_relationship_suggestions(db, player_id)
    return jsonify({
        'player_id': player_id,
        'player_name': p['name'],
        'manual': manual,
        'suggestions': suggestions,
        'rel_types': REL_TYPES,
        'initiative_levels': INITIATIVE_LEVELS,
        'table_styles': TABLE_STYLES,
    })


@app.route('/api/players/<int:player_id>/relationships', methods=['POST'])
@role_required('players')
def api_player_relationship_save(player_id):
    data = request.json or {}
    other_id = int(data.get('other_id'))
    rtype = data.get('relationship_type', 'neutral')
    score = int(data.get('relationship_score', 0) or 0)
    note = data.get('note', '')
    operator = g.user['name'] if g.user else 'unknown'
    if rtype not in ('positive', 'neutral', 'avoid'):
        return jsonify({'error': '关系类型错误'}), 400
    if other_id == player_id:
        return jsonify({'error': '不能与自己建立关系'}), 400
    rid = save_relationship(g.db, player_id, other_id, rtype, score, note, operator)
    return jsonify({'status': 'ok', 'id': rid})


@app.route('/api/players/<int:player_id>/relationships/<int:rel_id>', methods=['DELETE'])
@role_required('players')
def api_player_relationship_delete(player_id, rel_id):
    g.db.execute(
        'DELETE FROM player_relationships WHERE id=? AND (player_a_id=? OR player_b_id=?)',
        [rel_id, player_id, player_id]
    )
    g.db.commit()
    return jsonify({'status': 'ok'})


# =====================================================================
# V1.3 经营反馈闭环 + 桌局复盘
# =====================================================================

from operation_feedback import (submit_feedback, get_feedback, recompute_experience,
                                get_operation_review, generate_feedback_review_tasks,
                                generate_daily_snapshot, get_daily_snapshots,
                                CONFLICT_TYPE_LABELS as FB_CONFLICT_TYPES)
from table_learning import (recompute_pair_stats, get_pair_stats,
                            get_best_combinations, get_risk_combinations)

# ===== V1.5 数据分析中心 + AI 经营报告导出 =====
import analytics as analytics_mod


@app.route('/session_feedback')
@role_required('session_feedback')
def session_feedback_page():
    return render_template('session_feedback.html', active_page='session_feedback', user=g.user)


@app.route('/operation_review')
@role_required('operation_review')
def operation_review_page():
    return render_template('operation_review.html', active_page='operation_review', user=g.user)


@app.route('/api/sessions/list')
@role_required('session_feedback')
def api_sessions_list():
    db = g.db
    days = int(request.args.get('days', 14) or 14)
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = db.execute(
        '''SELECT s.id, s.start_time, s.status, m.name AS machine_name,
                  COUNT(sp.id) AS player_count
           FROM sessions s
           LEFT JOIN machines m ON s.machine_id = m.id
           LEFT JOIN session_players sp ON sp.session_id = s.id
           WHERE date(s.start_time) >= ?
           GROUP BY s.id
           ORDER BY s.start_time DESC
           LIMIT 100''',
        [since]
    ).fetchall()
    out = []
    for r in rows:
        r = dict(r)
        players = db.execute(
            '''SELECT DISTINCT p.name FROM session_players sp
               LEFT JOIN players p ON p.id = sp.player_id
               WHERE sp.session_id = ? AND sp.player_id IS NOT NULL''',
            [r['id']]
        ).fetchall()
        fb = db.execute('SELECT id FROM session_feedback WHERE session_id=?', [r['id']]).fetchone()
        r['players'] = [p['name'] for p in players]
        r['has_feedback'] = bool(fb)
        out.append(r)
    return jsonify({'sessions': out})


@app.route('/api/session_feedback/submit', methods=['POST'])
@role_required('session_feedback')
def api_session_feedback_submit():
    data = request.json or {}
    session_id = int(data.get('session_id'))
    operator = g.user['name'] if g.user else 'unknown'
    db = g.db
    sess = db.execute('SELECT id FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return jsonify({'error': 'session 不存在'}), 404
    rec = submit_feedback(db, session_id, data, operator)
    return jsonify({'status': 'ok', 'feedback': dict(rec)})


@app.route('/api/session_feedback/list')
@role_required('session_feedback')
def api_session_feedback_list():
    db = g.db
    limit = int(request.args.get('limit', 50) or 50)
    rows = get_feedback(db, limit=limit)
    return jsonify({'feedback': [dict(r) for r in rows]})


@app.route('/api/operation_review')
@role_required('operation_review')
def api_operation_review():
    db = g.db
    date_str = request.args.get('date')
    # 数据自动沉淀：若当天快照缺失则生成
    if date_str:
        snap = db.execute('SELECT id FROM daily_operation_snapshot WHERE date=?', [date_str]).fetchone()
        if not snap:
            generate_daily_snapshot(db, date_str)
    else:
        y = (date.today() - timedelta(days=1)).isoformat()
        snap = db.execute('SELECT id FROM daily_operation_snapshot WHERE date=?', [y]).fetchone()
        if not snap:
            generate_daily_snapshot(db, y)
    review = get_operation_review(db, date_str)
    return jsonify(review)


@app.route('/api/feedback/recompute', methods=['POST'])
@role_required('operation_review')
def api_feedback_recompute():
    db = g.db
    exp = recompute_experience(db)
    pairs = recompute_pair_stats(db)
    return jsonify({'status': 'ok', 'experience_updated': exp, 'pair_stats': pairs})


@app.route('/api/feedback/best-combinations')
@role_required('operation_review')
def api_feedback_best():
    return jsonify({'best_combinations': get_best_combinations(g.db, 20)})


@app.route('/api/feedback/risk-combinations')
@role_required('operation_review')
def api_feedback_risk():
    return jsonify({'risk_combinations': get_risk_combinations(g.db, 20)})


@app.route('/api/feedback/snapshot', methods=['POST'])
@role_required('operation_review')
def api_feedback_snapshot():
    db = g.db
    date_str = (request.json or {}).get('date')
    snap = generate_daily_snapshot(db, date_str)
    return jsonify({'status': 'ok', 'snapshot': snap})


@app.route('/api/feedback/generate-review-tasks', methods=['POST'])
@role_required('operation_review')
def api_feedback_gen_tasks():
    summary = generate_feedback_review_tasks(g.db)
    return jsonify({'status': 'ok', 'summary': summary})


# =====================================================================
# ===== V1.5 数据分析中心 + AI 经营报告导出 =====
# =====================================================================

@app.route('/analytics')
@role_required('analytics')
def analytics_page():
    return render_template('analytics.html', active_page='analytics', user=g.user)


def _parse_filter_args():
    """从请求参数解析时间筛选三件套。"""
    filter_type = request.args.get('filter_type', 'today') or 'today'
    if filter_type not in ('today', '7d', '30d', 'month', 'custom'):
        filter_type = 'today'
    custom_start = request.args.get('custom_start') or None
    custom_end = request.args.get('custom_end') or None
    return filter_type, custom_start, custom_end


@app.route('/api/analytics/dashboard')
@role_required('analytics')
def api_analytics_dashboard():
    filter_type, custom_start, custom_end = _parse_filter_args()
    try:
        data = analytics_mod.get_dashboard(g.db, filter_type, custom_start, custom_end)
        return jsonify({'status': 'ok', 'filter_type': filter_type,
                        'range': data.get('range'), 'data': data})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/analytics/export/json')
@role_required('analytics')
def api_analytics_export_json():
    date_str = request.args.get('date') or date.today().isoformat()
    try:
        blob, name = analytics_mod.export_json_package(g.db, date_str)
        return Response(
            blob,
            mimetype='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{name}"'}
        )
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/analytics/export/excel')
@role_required('analytics')
def api_analytics_export_excel():
    filter_type, custom_start, custom_end = _parse_filter_args()
    try:
        blob, name = analytics_mod.export_excel(g.db, filter_type, custom_start, custom_end)
        return Response(
            blob,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': f'attachment; filename="{name}"'}
        )
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


if __name__ == '__main__':
    debug_mode = os.environ.get('QCOS_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode)
