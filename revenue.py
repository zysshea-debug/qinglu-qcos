"""青鹭收银系统 - 真实实收收入口径 + 收银流水 + 作废/恢复/安全删除 (V1.6)

职责：
    1. 统一「真实实收 GMV」口径：一律按 business_date（= 真实支付 / 结账时间归属的
       Asia/Shanghai 日期）归属，绝不按 start_time / created_at。
    2. 跨午夜桌时拆分 split_session_duration_by_date()。
    3. 收银流水统一查询视图 iter_real_settlements()（穿透一致：分析中心 GMV == 流水合计）。
    4. 已结账桌台「作废 / 恢复」+「安全永久删除测试记录」。
    5. 所有函数作用在传入的 db 连接上（不自行打开连接），便于测试注入临时库 / fake clock。

设计约束（来自任务）：
    - 所有日期边界统一 Asia/Shanghai（本系统存储的均为门店本地 naive 时间，即 Asia/Shanghai）。
    - active / 未结账桌：不得进入真实实收 GMV，只能进入「进行中预计」。
    - voided 记录：必须从所有 GMV / 流水 / 分析 / 复盘 SQL 层排除（is_voided = 0）。
    - 原始记录必须保留（作废只是打标 + 审计，不物理删除）。
    - 永久删除只允许纯 TEST_DATA 且无任何真实资金影响，且由 admin 二次确认。
"""

from datetime import datetime, timedelta, date
from collections import OrderedDict

# ===================== 时区 / 日期助手 =====================

def shanghai_now():
    """当前 Asia/Shanghai 时间（本系统存储均为门店本地 naive 时间 = Asia/Shanghai）。"""
    return datetime.now()


def shanghai_today():
    return date.today()


def _as_dt(x):
    if isinstance(x, datetime):
        return x
    if isinstance(x, date):
        return datetime(x.year, x.month, x.day)
    if isinstance(x, str) and x.strip():
        try:
            return datetime.fromisoformat(x)
        except ValueError:
            return None
    return None


def business_date_for(dt=None):
    """给定时间 -> 归属营业日（Asia/Shanghai date 字符串 YYYY-MM-DD）。"""
    if dt is None:
        dt = shanghai_now()
    dt = _as_dt(dt)
    if dt is None:
        return None
    return dt.date().isoformat()


def split_session_duration_by_date(start, end):
    """把 [start, end) 桌时按自然日拆分，返回 { 'YYYY-MM-DD': minutes }。

    跨午夜 / 跨月 / 跨年均正确。start/end 可为 datetime / ISO 字符串。
    若 end <= start 返回 {}。
    """
    start = _at_midnight_boundary(start)
    end = _at_midnight_boundary(end)
    if start is None or end is None or end <= start:
        return {}
    result = {}
    cur = start
    while cur < end:
        next_day = cur.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        seg_end = min(end, next_day)
        minutes = (seg_end - cur).total_seconds() / 60.0
        d = cur.date().isoformat()
        result[d] = round(result.get(d, 0.0) + minutes, 2)
        cur = seg_end
    return result


def _at_midnight_boundary(x):
    dt = _as_dt(x)
    if dt is None:
        return None
    return dt


# ===================== 统一收银流水视图 =====================

# 作废原因枚举
VOID_REASONS = [
    'TEST_DATA',          # 测试数据
    'WRONG_CHECKOUT',     # 结账错误
    'DUPLICATE_RECORD',   # 重复记录
    'STAFF_ERROR',        # 员工操作失误
    'OTHER',              # 其他
]
VALID_VOID_REASONS = set(VOID_REASONS)

# 永久删除安全门槛：存在以下任意「真实资金影响」即禁止删除
_DELETE_BLOCKERS = [
    ('payments', 'payments', '第三方支付流水'),
    ('members', 'member_balance_movement', '会员余额变化'),
    ('product_sales', 'inventory', '商品库存影响'),
    ('discounts', 'coupon', '优惠券核销'),
]


def _settlement_query(include_voided):
    """返回 (sql, params_builder)。统一收银流水 = 三类结算的 UNION：

    A) 单人结账（session_players.status='checked_out' 且有 business_date）
    B) 整台结账（sessions.status='closed' 且有 business_date，且无单人结账玩家）
    C) 柜台商品（product_sales.session_player_id IS NULL 且 SETTLED 且有 business_date）
    """
    void_cond_sp = ' AND sp.is_voided = 0' if not include_voided else ''
    void_cond_s = ' AND s.is_voided = 0' if not include_voided else ''
    void_cond_ps = ' AND ps.is_voided = 0' if not include_voided else ''

    sql = '''
    SELECT 'player' AS kind,
           sp.id AS sp_id, sp.session_id, s.machine_id,
           sp.player_name, sp.player_id,
           sp.start_time, sp.end_time, sp.duration_minutes,
           sp.fee, sp.final_fee,
           COALESCE(sp.product_total, 0) AS product_total,
           sp.discount_amount, sp.grand_total,
           sp.payment_method, sp.business_date, sp.paid_at, sp.is_voided,
           sp.operator AS operator,
           s.is_overnight, m.name AS machine_name, m.type AS machine_type,
           s.void_reason AS void_reason, s.voided_by AS voided_by, s.voided_at AS voided_at
    FROM session_players sp
    JOIN sessions s ON sp.session_id = s.id
    LEFT JOIN machines m ON s.machine_id = m.id
    WHERE sp.status = 'checked_out' AND sp.business_date IS NOT NULL
    {void_sp}

    UNION ALL

    SELECT 'session' AS kind,
           NULL AS sp_id, s.id AS session_id, s.machine_id,
           (SELECT GROUP_CONCAT(player_name, ', ') FROM session_players WHERE session_id = s.id) AS player_name,
           NULL AS player_id,
           s.start_time, s.end_time, s.duration_minutes,
           s.fee, s.final_fee,
           (SELECT COALESCE(SUM(total), 0) FROM product_sales WHERE session_id = s.id
              AND NOT (session_player_id IS NULL AND business_date IS NOT NULL AND status = 'SETTLED')) AS product_total,
           s.discount_amount,
           (s.final_fee + COALESCE((SELECT SUM(total) FROM product_sales WHERE session_id = s.id
              AND NOT (session_player_id IS NULL AND business_date IS NOT NULL AND status = 'SETTLED')), 0)) AS grand_total,
           s.payment_method, s.business_date, s.paid_at, s.is_voided,
           s.operator AS operator,
           s.is_overnight, m.name AS machine_name, m.type AS machine_type,
           s.void_reason AS void_reason, s.voided_by AS voided_by, s.voided_at AS voided_at
    FROM sessions s
    LEFT JOIN machines m ON s.machine_id = m.id
    WHERE s.status = 'closed' AND s.business_date IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM session_players sp2
          WHERE sp2.session_id = s.id AND sp2.status = 'checked_out' AND sp2.business_date IS NOT NULL
      )
    {void_s}

    UNION ALL

    SELECT 'product' AS kind,
           NULL AS sp_id, ps.session_id, s.machine_id,
           '柜台' AS player_name, NULL AS player_id,
           ps.created_at AS start_time, ps.settled_at AS end_time, NULL AS duration_minutes,
           0 AS fee, 0 AS final_fee, ps.total AS product_total, 0 AS discount_amount, ps.total AS grand_total,
           COALESCE(ps.payment_method, s.payment_method) AS payment_method,
           ps.business_date, ps.settled_at AS paid_at, ps.is_voided,
           s.operator AS operator,
           s.is_overnight, m.name AS machine_name, m.type AS machine_type,
           s.void_reason AS void_reason, s.voided_by AS voided_by, s.voided_at AS voided_at
    FROM product_sales ps
    LEFT JOIN sessions s ON ps.session_id = s.id
    LEFT JOIN machines m ON s.machine_id = m.id
    WHERE ps.session_player_id IS NULL AND ps.status = 'SETTLED' AND ps.business_date IS NOT NULL
    {void_ps}
    '''
    sql = sql.format(void_sp=void_cond_sp, void_s=void_cond_s, void_ps=void_cond_ps)
    return sql


def iter_real_settlements(db, business_date=None, include_voided=False):
    """返回某营业日（或全量）真实结算行列表（dict）。

    business_date=None 表示不限日期（用于全量流水 / 导出）。
    穿透一致：分析中心 GMV 与流水合计均来自本视图。

    注意：UNION 的三支各自的 business_date 来自不同表（sp/s/ps），
    直接在外层拼 `AND (business_date=?)` 会绑定到末支 SELECT 并产生
    歧义列名。因此把 UNION 包成派生表，再在外层用 `sub.business_date`
    过滤——派生表列唯一，不存在歧义。
    """
    inner = _settlement_query(include_voided)
    if business_date is not None:
        sql = "SELECT * FROM (\n" + inner + "\n) sub WHERE sub.business_date = ?"
        rows = db.execute(sql, [business_date]).fetchall()
    else:
        rows = db.execute(inner).fetchall()
    return [dict(r) for r in rows]


# ===================== 历史兼容统一视图（V1.6.1） =====================
# 目标：V1.6 之前已存在的真实结账记录（NATIVE_PRE_V16）与 Excel 历史导入
#       （LEGACY_IMPORT）不能因 business_date / paid_at 缺失而从收银流水消失。
# 原则：
#   - 不修改真实历史数据（不做 backfill）；
#   - 不伪造 paid_at / payment_method；
#   - 日期只用于「历史可见性」归档（date_source 明确标注推断来源）；
#   - 金额必须有可信来源（amount_source），否则 VISIBLE_INCOMPLETE（不伪造 0 元）；
#   - 历史兼容行不计入真实 GMV（analytics 仍走 iter_real_settlements）。

def _has_column(db, table, col):
    cols = [r['name'] for r in db.execute('PRAGMA table_info(%s)' % table).fetchall()]
    return col in cols


def _to_num(v):
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _history_date(p, s):
    """历史兼容日期 fallback（仅用于历史可见性归档，非真实支付日）。

    优先级：sp.business_date → date(sp.paid_at) → date(sp.end_time)
            → date(session.end_time) → date(session.start_time)。
    玩家级 end_time 优先于桌级（旧系统可能存在单人提前结账）。
    返回 (date_str, date_source)。
    """
    p = p or {}
    s = s or {}
    for key, src in [
        ('business_date', 'BUSINESS_DATE'),
        ('paid_at', 'PAID_AT'),
        ('end_time', 'PLAYER_END_TIME'),
    ]:
        v = p.get(key)
        if v:
            return str(v)[:10], src
    for key, src in [
        ('end_time', 'SESSION_END_TIME'),
        ('start_time', 'LEGACY_DATE'),
    ]:
        v = s.get(key)
        if v:
            return str(v)[:10], src
    return None, 'NONE'


def _norm_history_row(**kw):
    """构造 normalized 历史兼容结算行（公共字段默认值）。"""
    base = {
        'kind': 'player', 'sp_id': None, 'session_id': None, 'machine_id': None,
        'player_id': None, 'player_name': '', 'start_time': None, 'end_time': None,
        'duration_minutes': None, 'fee': None, 'final_fee': None, 'product_total': 0,
        'discount_amount': 0, 'grand_total': None, 'payment_method': None,
        'paid_at': None, 'is_voided': 0, 'operator': None, 'is_overnight': 0,
        'machine_name': None, 'machine_type': None, 'void_reason': None,
        'voided_by': None, 'voided_at': None,
        # V1.6.1 normalized 字段
        'source_type': None, 'historical_compat': 1, 'legacy': 0,
        'visibility_state': None, 'date_source': None, 'amount_source': None,
        'real_business_date': None, 'display_time': None, 'amount': None,
    }
    base.update(kw)
    return base


def _settlement_history_rows(db):
    """NATIVE_PRE_V16 + LEGACY_IMPORT 历史兼容结算行（normalized）。

    A) NATIVE_PRE_V16：V1.6 之前的原生 QCOS 已结账记录
       （session_players.status='checked_out' 且无 business_date，且非 Excel
       legacy 导入）。逐玩家归一化：
         - 金额：sp.grand_total（EXACT）→ sp.final_fee+product_total（PARTIAL）
           → 该桌所有玩家均无个人金额但 session.final_fee 可信时出桌级一行
           （SESSION_FINAL_FEE，PARTIAL，玩家级显示历史未拆分）
           → 完全无可信金额：VISIBLE_INCOMPLETE（amount=None，不伪造 0 元）。
         - 日期：_history_date fallback（date_source 标注）。
         - paid_at 一律 None（不伪造）；display_time=归档时间（end_time）。
    B) LEGACY_IMPORT：Excel 历史导入（source_id 非空 或 time_precision='date_only'），
       兼容 status='completed' 与 'closed'，逐桌一行，金额 = session.final_fee。
    """
    out = []
    sp_has_bd = _has_column(db, 'session_players', 'business_date')
    sp_has_void = _has_column(db, 'session_players', 'is_voided')
    sp_has_operator = _has_column(db, 'session_players', 'operator')
    s_has_void = _has_column(db, 'sessions', 'is_voided')
    s_has_tp = _has_column(db, 'sessions', 'time_precision')

    # ---------- A) NATIVE_PRE_V16 ----------
    bd_cond = ' AND sp.business_date IS NULL' if sp_has_bd else ''
    void_sp = ' AND sp.is_voided = 0' if sp_has_void else ''
    void_s = ' AND s.is_voided = 0' if s_has_void else ''
    rows = db.execute('''
        SELECT sp.id AS sp_id, sp.session_id, sp.player_id, sp.player_name,
               sp.start_time, sp.end_time, sp.duration_minutes,
               sp.fee, sp.final_fee, sp.product_total, sp.discount_amount,
               sp.grand_total, sp.payment_method, sp.operator AS sp_operator,
               s.machine_id, s.start_time AS s_start, s.end_time AS s_end,
               s.final_fee AS s_final_fee, s.payment_method AS s_pm,
               s.is_overnight, s.operator AS s_operator,
               m.name AS machine_name, m.type AS machine_type
        FROM session_players sp
        JOIN sessions s ON sp.session_id = s.id
        LEFT JOIN machines m ON s.machine_id = m.id
        WHERE sp.status = 'checked_out'
          AND s.status IN ('closed', 'completed')
          AND (s.source_id IS NULL OR s.source_id = '')
          {bd_cond} {void_sp} {void_s}
        ORDER BY sp.id
    '''.format(bd_cond=bd_cond, void_sp=void_sp, void_s=void_s)).fetchall()

    by_session = OrderedDict()
    for r in rows:
        r = dict(r)
        by_session.setdefault(r['session_id'], []).append(r)

    for sid, players in by_session.items():
        s = players[0]
        no_personal = all(
            (_to_num(p.get('grand_total')) is None)
            and (_to_num(p.get('final_fee')) in (None, 0))
            and (_to_num(p.get('product_total')) in (None, 0))
            for p in players
        )
        s_final = _to_num(s.get('s_final_fee'))
        if no_personal and s_final is not None and s_final > 0:
            # 整桌收款：桌级一行（金额不拆到个人，避免平均分摊伪造）
            names = '、'.join(p['player_name'] for p in players if p['player_name'])
            date_val, date_src = _history_date(None, s)
            out.append(_norm_history_row(
                kind='session', sp_id=None, session_id=sid, player_name=names,
                machine_id=s.get('machine_id'), machine_name=s.get('machine_name'),
                machine_type=s.get('machine_type'), is_overnight=s.get('is_overnight'),
                start_time=s.get('s_start'), end_time=s.get('s_end'),
                duration_minutes=s.get('duration_minutes'),
                fee=s_final, final_fee=s_final, product_total=0,
                discount_amount=_to_num(s.get('discount_amount')) or 0,
                grand_total=s_final, amount=s_final,
                amount_source='SESSION_FINAL_FEE', visibility_state='VISIBLE_PARTIAL',
                payment_method=s.get('s_pm'), operator=s.get('s_operator') or '历史未记录',
                business_date=date_val, date_source=date_src,
                display_time=s.get('s_end'), is_voided=0,
                source_type='NATIVE_PRE_V16',
            ))
            continue
        for p in players:
            grand = _to_num(p.get('grand_total'))
            fin = _to_num(p.get('final_fee'))
            prod = _to_num(p.get('product_total'))
            if grand is not None:
                amount, a_src, vis = grand, 'SP_GRAND_TOTAL', 'VISIBLE_EXACT'
            elif (fin is not None and fin > 0) or (prod is not None and prod > 0):
                amount = round(float(fin or 0) + float(prod or 0), 2)
                a_src, vis = 'SP_FINAL_FEE', 'VISIBLE_PARTIAL'
            else:
                amount, a_src, vis = None, 'NONE', 'VISIBLE_INCOMPLETE'
            date_val, date_src = _history_date(p, s)
            out.append(_norm_history_row(
                kind='player', sp_id=p['sp_id'], session_id=sid,
                player_id=p.get('player_id'), player_name=p.get('player_name') or '',
                machine_id=s.get('machine_id'), machine_name=s.get('machine_name'),
                machine_type=s.get('machine_type'), is_overnight=s.get('is_overnight'),
                start_time=p.get('start_time'), end_time=p.get('end_time'),
                duration_minutes=p.get('duration_minutes'),
                fee=p.get('fee'), final_fee=p.get('final_fee'),
                product_total=prod or 0, discount_amount=p.get('discount_amount') or 0,
                grand_total=amount, amount=amount,
                amount_source=a_src, visibility_state=vis,
                payment_method=p.get('payment_method') or s.get('s_pm'),
                operator=(p.get('sp_operator') if sp_has_operator else None) or s.get('s_operator') or '历史未记录',
                business_date=date_val, date_source=date_src,
                display_time=p.get('end_time') or s.get('s_end'),
                is_voided=0, source_type='NATIVE_PRE_V16',
            ))

    # ---------- B) LEGACY_IMPORT ----------
    tp_cond = " OR s.time_precision = 'date_only'" if s_has_tp else ''
    void_l = ' AND s.is_voided = 0' if s_has_void else ''
    leg = db.execute('''
        SELECT s.*, m.name AS machine_name, m.type AS machine_type
        FROM sessions s
        LEFT JOIN machines m ON s.machine_id = m.id
        WHERE s.status IN ('closed', 'completed')
          AND (s.source_id IS NOT NULL OR s.source_id != ''{tp})
          {void_l}
        ORDER BY s.id
    '''.format(tp=tp_cond, void_l=void_l)).fetchall()
    for r in leg:
        r = dict(r)
        st = r.get('start_time') or ''
        bd = st[:10] if len(st) >= 10 else None
        fin = _to_num(r.get('final_fee'))
        out.append(_norm_history_row(
            kind='legacy', sp_id=None, session_id=r['id'], player_name='',
            machine_id=r.get('machine_id'), machine_name=r.get('machine_name'),
            machine_type=r.get('machine_type'), is_overnight=r.get('is_overnight'),
            start_time=r.get('start_time'), end_time=r.get('end_time'),
            duration_minutes=r.get('duration_minutes'),
            fee=r.get('fee'), final_fee=r.get('final_fee'), product_total=0,
            discount_amount=_to_num(r.get('discount_amount')) or 0,
            grand_total=fin, amount=fin,
            amount_source='LEGACY_IMPORT' if fin is not None else 'NONE',
            visibility_state='VISIBLE_PARTIAL' if fin is not None else 'VISIBLE_INCOMPLETE',
            payment_method=None, operator='历史导入',
            business_date=bd, date_source='LEGACY_DATE',
            display_time=r.get('end_time'),
            is_voided=r.get('is_voided') or 0,
            void_reason=r.get('void_reason'), voided_by=r.get('voided_by'),
            voided_at=r.get('voided_at'),
            legacy=1, source_type='LEGACY_IMPORT',
        ))
    return out


def iter_settlement_rows(db, include_voided=False):
    """统一收银流水视图（normalized，含历史兼容）：

      - NATIVE_V16：iter_real_settlements 全部行（真实 GMV 口径，business_date 严格）
      - NATIVE_PRE_V16：V1.6 之前原生结账（无 business_date）→ 历史兼容
      - LEGACY_IMPORT：Excel 历史导入（completed/closed 均兼容）→ 历史兼容

    每行统一带 source_type / historical_compat / visibility_state /
    date_source / amount_source / real_business_date / display_time / amount，
    页面层不再各自写兼容判断。

    注意：历史兼容行不计入真实 GMV——分析中心 / 月度报表仍走
    iter_real_settlements（business_date 硬过滤），保证「历史可见」与
    「真实实收 GMV」严格分离。
    """
    rows = [dict(r) for r in iter_real_settlements(db, business_date=None, include_voided=include_voided)]
    for r in rows:
        r['source_type'] = 'NATIVE_V16'
        r['historical_compat'] = 0
        r['legacy'] = 0
        r['visibility_state'] = 'VISIBLE_EXACT'
        r['date_source'] = 'BUSINESS_DATE' if r.get('business_date') else ('PAID_AT' if r.get('paid_at') else 'NONE')
        r['amount_source'] = (
            'SP_GRAND_TOTAL' if r['kind'] == 'player'
            else 'SESSION_FINAL_FEE' if r['kind'] == 'session' else 'PAYMENT')
        r['real_business_date'] = r.get('business_date')
        r['display_time'] = r.get('paid_at') or r.get('end_time') or ''
        r['amount'] = r.get('grand_total')
        r.setdefault('payment_label', '')
    rows.extend(_settlement_history_rows(db))
    return rows


def real_gmv(db, business_date, include_voided=False):
    """某营业日真实实收 GMV（= 该日所有结算 grand_total 之和，穿透一致）。"""
    rows = iter_real_settlements(db, business_date, include_voided=include_voided)
    return round(sum(float(r.get('grand_total') or 0) for r in rows), 2)


def real_gmv_range(db, start, end, include_voided=False):
    """区间 [start, end]（含边界，date 字符串）真实实收 GMV。逐日求和。"""
    d0 = _as_dt(start).date()
    d1 = _as_dt(end).date()
    total = 0.0
    cur = d0
    one = timedelta(days=1)
    while cur <= d1:
        total += real_gmv(db, cur.isoformat(), include_voided=include_voided)
        cur += one
    return round(total, 2)


def in_progress_estimate(db, now=None):
    """当前进行中桌的「预计」应收（不含任何已结账真实 GMV）。

    口径：当前所有 active 桌中，所有**尚未结账（status='playing'）**玩家截至当前的
        「预计台费 + 未结商品消费」的合计（玩家级汇总），另加桌级柜台未结算商品。
      - 预计台费：复用正式计费引擎 billing.calculate_fee，且与单人结账 preview 完全一致：
          玩家自己的 start_time + 整桌 effective_end_time + 整桌 is_overnight + 库内 settings。
      - 未结商品：session_player_id 归属该玩家且 status='UNSETTLED'（已 SETTLED 不计）。
      - 桌级柜台商品：session_player_id IS NULL 且 status='UNSETTLED'（防重复，不按整桌 SUM）。
    已单人结账（checked_out）玩家不再计入；已结算商品不再计入。
    这是「预计」不是「真实」，UI 必须明确区分。
    """
    now = now or shanghai_now()
    # 与台桌总览 / 单人结账 preview 同源的库内设置（而非硬编码默认值）
    settings = {}
    for row in db.execute('SELECT key, value FROM settings').fetchall():
        val = row['value']
        try:
            settings[row['key']] = int(val)
        except (ValueError, TypeError):
            try:
                settings[row['key']] = float(val)
            except (ValueError, TypeError):
                settings[row['key']] = val

    rows = db.execute(
        "SELECT * FROM sessions WHERE status='active'"
    ).fetchall()
    total = 0.0
    for r in rows:
        r = dict(r)
        # 延迟 import 避免循环依赖
        import billing
        import overnight
        # 与单人结账 preview 相同：整桌 effective_end_time + 整桌 is_overnight
        end = overnight.effective_end_time(r, now=now)
        effective_is_overnight = bool(r.get('is_overnight'))
        machine_type = _machine_type(db, r['machine_id'])

        # 1) 未结账（playing）玩家逐人累计：预计台费 + 未结商品
        players = db.execute(
            "SELECT * FROM session_players WHERE session_id=? AND status='playing'",
            [r['id']]
        ).fetchall()
        for p in players:
            p = dict(p)
            start = _as_dt(p.get('start_time'))
            if start is None:
                continue
            fee, _ = billing.calculate_fee(
                machine_type, start, end, settings, is_overnight=effective_is_overnight
            )
            prod = db.execute(
                "SELECT COALESCE(SUM(total), 0) t FROM product_sales "
                "WHERE session_player_id=? AND status='UNSETTLED'",
                [p['id']]
            ).fetchone()['t'] or 0
            total += round(float(fee or 0) + float(prod or 0), 2)

        # 2) 桌级柜台未结算商品（挂桌不挂玩家；按 session_player_id IS NULL 防重复）
        counter = db.execute(
            "SELECT COALESCE(SUM(total), 0) t FROM product_sales "
            "WHERE session_id=? AND session_player_id IS NULL AND status='UNSETTLED'",
            [r['id']]
        ).fetchone()['t'] or 0
        total += round(float(counter or 0), 2)
    return round(total, 2)


def _machine_type(db, machine_id):
    m = db.execute('SELECT type FROM machines WHERE id=?', [machine_id]).fetchone()
    return m['type'] if m else '8port'


def closed_sessions_on_date(db, business_date):
    """某营业日已结账桌数（按 session 级 business_date 去重计数）。"""
    rows = db.execute(
        "SELECT COUNT(DISTINCT id) c FROM sessions WHERE business_date=? AND status='closed'",
        [business_date]
    ).fetchone()
    return rows['c'] if rows else 0


def active_session_count(db):
    return db.execute("SELECT COUNT(*) c FROM sessions WHERE status='active'").fetchone()['c']


# ===================== 作废 / 恢复 =====================

def _write_cashier_audit(db, action, session_id, operator, reason=None, snapshot=None, now=None):
    now = now or shanghai_now()
    db.execute(
        '''INSERT INTO cashier_audit
           (action, session_id, operator, reason, snapshot, changed_at)
           VALUES (?, ?, ?, ?, ?, ?)''',
        [action, session_id, operator or 'unknown', reason or '',
         snapshot if snapshot is not None else '{}', now.isoformat(timespec='seconds')]
    )


def void_session(db, session_id, reason, operator, now=None):
    """作废一桌已结账流水（admin）。打标 + 级联 + 审计；不物理删除。

    级联：sessions + 其下 session_players + 其下 product_sales 全部 is_voided=1。
    原始金额 / 支付方式 / 结账时间保留（仅打标，供已作废视图与恢复使用）。
    返回被修改的 session 行（dict）。
    """
    if reason not in VALID_VOID_REASONS:
        reason = 'OTHER'
    now = now or shanghai_now()
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return None
    sess = dict(sess)
    if sess.get('is_voided'):
        return sess  # 幂等：已作废直接返回
    snapshot = _snapshot_session(db, session_id)
    db.execute(
        "UPDATE sessions SET is_voided=1, void_reason=?, voided_by=?, voided_at=? WHERE id=?",
        [reason, operator, now.isoformat(timespec='seconds'), session_id]
    )
    db.execute("UPDATE session_players SET is_voided=1 WHERE session_id=?", [session_id])
    db.execute("UPDATE product_sales SET is_voided=1 WHERE session_id=?", [session_id])
    _write_cashier_audit(db, 'void', session_id, operator, reason=reason, snapshot=snapshot, now=now)
    return db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()


def restore_session(db, session_id, operator, now=None):
    """恢复被作废的流水（admin）。重新进入 GMV / 流水 / 分析。记录恢复审计。"""
    now = now or shanghai_now()
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return None
    sess = dict(sess)
    if not sess.get('is_voided'):
        return sess  # 幂等：未作废直接返回
    snapshot = _snapshot_session(db, session_id)
    db.execute(
        "UPDATE sessions SET is_voided=0, void_reason=NULL, voided_by=NULL, voided_at=NULL WHERE id=?",
        [session_id]
    )
    db.execute("UPDATE session_players SET is_voided=0 WHERE session_id=?", [session_id])
    db.execute("UPDATE product_sales SET is_voided=0 WHERE session_id=?", [session_id])
    _write_cashier_audit(db, 'restore', session_id, operator, snapshot=snapshot, now=now)
    return db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()


def _snapshot_session(db, session_id):
    """删除前 / 重要操作前，完整快照（便于追溯，不丢失资金信息）。"""
    import json
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    sps = db.execute('SELECT * FROM session_players WHERE session_id=?', [session_id]).fetchall()
    prods = db.execute('SELECT * FROM product_sales WHERE session_id=?', [session_id]).fetchall()
    pays = db.execute(
        "SELECT * FROM payments WHERE session_player_id IN (SELECT id FROM session_players WHERE session_id=?)",
        [session_id]
    ).fetchall()
    return json.dumps({
        'session': [dict(sess)] if sess else [],
        'session_players': [dict(r) for r in sps],
        'product_sales': [dict(r) for r in prods],
        'payments': [dict(r) for r in pays],
    }, ensure_ascii=False, default=str)


# ===================== 永久删除安全门槛 =====================

def can_permanently_delete(db, session_id):
    """检查是否允许永久删除（仅纯 TEST_DATA 且无真实资金影响）。

    返回 (allowed: bool, code: str, message: str)。
    code:
      OK / NOT_TEST_DATA / HAS_PAYMENT / HAS_MEMBER_BALANCE /
      HAS_INVENTORY / HAS_COUPON / HAS_OTHER_FINANCIAL
    """
    sess = db.execute('SELECT * FROM sessions WHERE id=?', [session_id]).fetchone()
    if not sess:
        return False, 'NOT_FOUND', '桌局不存在'
    sess = dict(sess)

    # 1) 必须是 TEST_DATA 作废来源（只有被标记为测试数据、且已作废的才允许）
    if not sess.get('is_voided') or sess.get('void_reason') != 'TEST_DATA':
        return False, 'NOT_TEST_DATA', '仅允许永久删除「已作废且原因=TEST_DATA」的测试记录'

    # 2) 第三方支付流水（payments 表有该桌玩家记录）
    pays = db.execute(
        "SELECT COUNT(*) c FROM payments WHERE session_player_id IN "
        "(SELECT id FROM session_players WHERE session_id=?)",
        [session_id]
    ).fetchone()['c']
    if pays and pays > 0:
        return False, 'HAS_PAYMENT', '存在第三方支付流水，禁止永久删除'

    # 3) 会员余额变化（members.total_spent 是否被该桌减少 / recharge 影响）
    sp_ids = [r['id'] for r in db.execute(
        'SELECT id FROM session_players WHERE session_id=?', [session_id]).fetchall()]
    if sp_ids:
        member_moves = db.execute(
            "SELECT COUNT(*) c FROM members WHERE id IN "
            "(SELECT DISTINCT member_id FROM product_sales WHERE session_id=?) "
            "OR id IN (SELECT member_id FROM product_sales WHERE session_player_id IN (%s))"
            % ','.join('?' * len(sp_ids)),
            [session_id] + sp_ids
        ).fetchone()['c']
        if member_moves and member_moves > 0:
            # 仅当确实有会员余额支付（payment_method='member'）才视为资金影响
            member_pay = db.execute(
                "SELECT COUNT(*) c FROM session_players WHERE session_id=? AND payment_method='member'",
                [session_id]
            ).fetchone()['c']
            if member_pay and member_pay > 0:
                return False, 'HAS_MEMBER_BALANCE', '存在会员余额支付，禁止永久删除'

    # 4) 优惠券核销（discounts.used_session_id 指向本桌）
    disc = db.execute(
        "SELECT COUNT(*) c FROM discounts WHERE used_session_id=?", [session_id]
    ).fetchone()['c']
    if disc and disc > 0:
        return False, 'HAS_COUPON', '存在优惠券核销，禁止永久删除'

    # 5) 商品库存影响（商品为实物且有库存扣减记录）—— 仅当商品非「无码/自定义」且曾售出
    inv = db.execute(
        "SELECT COUNT(*) c FROM product_sales WHERE session_id=? AND is_custom=0 AND product_id IS NOT NULL",
        [session_id]
    ).fetchone()['c']
    if inv and inv > 0:
        # 仅当系统开启了库存管理才视为影响；此处保守地要求无真实库存商品销售
        return False, 'HAS_INVENTORY', '存在真实商品销售（可能影响库存），禁止永久删除'

    return True, 'OK', '允许永久删除（纯测试数据，无真实资金影响）'


def permanently_delete_session(db, session_id, operator, now=None, confirmed=False):
    """永久删除测试桌局（admin，需二次确认）。

    前置：can_permanently_delete 必须 OK；confirmed 必须为 True（二次确认）。
    删除前写入完整审计快照（cashier_audit action='delete'）。
    返回 (ok, code, message)。
    """
    if not confirmed:
        return False, 'NEED_CONFIRM', '必须二次确认：此操作将永久删除测试桌台，无法恢复'
    allowed, code, msg = can_permanently_delete(db, session_id)
    if not allowed:
        return False, code, msg
    now = now or shanghai_now()
    snapshot = _snapshot_session(db, session_id)
    # 先删子表（外键约束），再删主表
    db.execute("DELETE FROM product_sales WHERE session_id=?", [session_id])
    db.execute("DELETE FROM session_players WHERE session_id=?", [session_id])
    db.execute("DELETE FROM end_time_audit WHERE session_id=?", [session_id])
    db.execute("DELETE FROM session_feedback WHERE session_id=?", [session_id])
    db.execute("DELETE FROM cashier_audit WHERE session_id=?", [session_id])
    db.execute("DELETE FROM sessions WHERE id=?", [session_id])
    _write_cashier_audit(db, 'delete', session_id, operator, snapshot=snapshot, now=now)
    return True, 'OK', '已永久删除测试桌局'
