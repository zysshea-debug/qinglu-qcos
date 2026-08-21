"""青鹭收银系统 - 数据库模型与初始化 (Phase 2)"""

import os
import sqlite3
import hashlib
from config import MACHINES, DEFAULT_SETTINGS, DB_PATH, DEFAULT_PRODUCTS, ROLES


def _ensure_default_admin(db):
    """首次初始化时创建默认管理员。
    安全约束：绝不硬编码 admin123；密码优先取自环境变量 QCOS_ADMIN_PASSWORD，
    缺失时生成一次性强随机密码并打印安全提示（绝不回退到 admin123）。
    仅在 users 表中不存在 admin 时由 init_db 调用，绝不修改已有管理员密码。
    """
    import secrets
    pw = os.environ.get('QCOS_ADMIN_PASSWORD', '').strip()
    if not pw:
        pw = secrets.token_urlsafe(16)
        print(
            '[QCOS 安全提示] 未设置环境变量 QCOS_ADMIN_PASSWORD，已为初始管理员生成一次性强随机密码：\n'
            '    ' + pw + '\n'
            '    请尽快登录后在「用户管理」中修改，或在 .env 中设置 QCOS_ADMIN_PASSWORD 后重建数据库。'
        )
    now = sqlite3.connect(DB_PATH).execute('SELECT datetime("now")').fetchone()[0]
    db.execute(
        'INSERT INTO users (username, password_hash, name, role, created_at) VALUES (?, ?, ?, ?, ?)',
        ['admin', hash_password(pw), '管理员', 'admin', now]
    )


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()


def init_db():
    db = get_db()
    db.executescript('''
        -- ===== Phase 1 表 =====
        CREATE TABLE IF NOT EXISTS machines (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            status TEXT DEFAULT 'idle',
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id INTEGER NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_minutes INTEGER,
            fee REAL,
            fee_breakdown TEXT,
            discount_type TEXT,
            discount_id INTEGER,
            discount_amount REAL DEFAULT 0,
            final_fee REAL,
            payment_method TEXT,
            status TEXT DEFAULT 'active',
            note TEXT,
            FOREIGN KEY (machine_id) REFERENCES machines(id)
        );

        CREATE TABLE IF NOT EXISTS session_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            player_name TEXT NOT NULL,
            player_id INTEGER,
            is_organizer INTEGER DEFAULT 0,
            visit_type TEXT DEFAULT 'active',
            is_overnight INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (player_id) REFERENCES players(id)
        );

        CREATE TABLE IF NOT EXISTS discounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lottery_date TEXT NOT NULL,
            player_name TEXT NOT NULL,
            discount_type TEXT NOT NULL,
            max_deduction REAL NOT NULL,
            used INTEGER DEFAULT 0,
            used_session_id INTEGER,
            created_at TEXT,
            FOREIGN KEY (used_session_id) REFERENCES sessions(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        -- ===== Phase 2 新增表 =====

        -- 玩家库 (关联QCOS)
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            wechat TEXT,
            qcos_id TEXT,
            dan TEXT,
            dan_source TEXT DEFAULT 'self',
            first_visit TEXT,
            notes TEXT,
            is_member INTEGER DEFAULT 0,
            member_id INTEGER,
            created_at TEXT,
            updated_at TEXT,
            status TEXT DEFAULT 'active',
            archived_at TEXT,
            archive_reason TEXT
        );

        -- 会员储值
        CREATE TABLE IF NOT EXISTS members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            phone TEXT,
            pin_hash TEXT,
            balance REAL DEFAULT 0,
            total_recharge REAL DEFAULT 0,
            total_spent REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (player_id) REFERENCES players(id)
        );

        -- 充值记录
        CREATE TABLE IF NOT EXISTS recharge_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            member_id INTEGER NOT NULL,
            amount REAL NOT NULL,
            balance_before REAL,
            balance_after REAL,
            payment_method TEXT,
            note TEXT,
            operator TEXT,
            created_at TEXT,
            FOREIGN KEY (member_id) REFERENCES members(id)
        );

        -- 商品
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'drink',
            price REAL NOT NULL,
            cost REAL DEFAULT 0,
            stock INTEGER DEFAULT -1,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );

        -- 商品销售记录
        CREATE TABLE IF NOT EXISTS product_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER,
            session_player_id INTEGER,
            product_id INTEGER,
            product_name TEXT NOT NULL,
            category TEXT,
            price REAL NOT NULL,
            quantity INTEGER DEFAULT 1,
            total REAL NOT NULL,
            is_custom INTEGER DEFAULT 0,
            custom_category TEXT,
            payment_method TEXT,
            member_id INTEGER,
            created_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id),
            FOREIGN KEY (product_id) REFERENCES products(id),
            FOREIGN KEY (member_id) REFERENCES members(id)
        );

        -- 系统用户 (权限)
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            is_active INTEGER DEFAULT 1,
            created_at TEXT,
            last_login TEXT
        );

        -- 历史到店记录 (从Excel原始组局记录导入)
        CREATE TABLE IF NOT EXISTS visit_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            player_name TEXT,
            visit_date TEXT,
            machine_type TEXT,
            game_type TEXT,
            brought_guest INTEGER DEFAULT 0,
            organizer_name TEXT,
            is_overnight INTEGER DEFAULT 0,
            table_number INTEGER,
            is_table_head INTEGER DEFAULT 0,
            table_head_organizer TEXT,
            data_quality TEXT,
            created_at TEXT,
            FOREIGN KEY (player_id) REFERENCES players(id)
        );

        -- ===== Phase 3: 组局与组织者系统 =====

        -- 场务人员
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            name TEXT NOT NULL,
            staff_type TEXT DEFAULT 'entertainment',
            commission_rate REAL DEFAULT 0,
            status TEXT DEFAULT 'active',
            joined_date TEXT,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (player_id) REFERENCES players(id)
        );

        -- 激励奖金档位
        CREATE TABLE IF NOT EXISTS incentive_tiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            min_amount REAL NOT NULL,
            max_amount REAL,
            bonus_amount REAL NOT NULL,
            description TEXT,
            is_active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        );

        -- 场务结算记录
        CREATE TABLE IF NOT EXISTS staff_settlements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id INTEGER NOT NULL,
            settlement_date TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            total_gmv REAL DEFAULT 0,
            commission_rate REAL DEFAULT 0,
            commission_amount REAL DEFAULT 0,
            incentive_tier_id INTEGER,
            incentive_amount REAL DEFAULT 0,
            total_payout REAL DEFAULT 0,
            status TEXT DEFAULT 'pending',
            paid_at TEXT,
            note TEXT,
            created_at TEXT,
            FOREIGN KEY (staff_id) REFERENCES staff(id),
            FOREIGN KEY (incentive_tier_id) REFERENCES incentive_tiers(id)
        );

        -- ===== 竞争情报系统 (Competitive Intelligence) =====

        -- M1: 竞争店基础信息
        CREATE TABLE IF NOT EXISTS ci_competitors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT,
            open_date TEXT,
            area_sqm REAL,
            machine_count INTEGER,
            table_4port INTEGER DEFAULT 0,
            table_8port INTEGER DEFAULT 0,
            positioning TEXT,
            target_customers TEXT,
            key_selling_points TEXT,
            known_advantages TEXT,
            known_weaknesses TEXT,
            is_self INTEGER DEFAULT 0,
            updated_at TEXT,
            created_at TEXT
        );

        -- M2: 价格体系
        CREATE TABLE IF NOT EXISTS ci_pricing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            record_date TEXT NOT NULL,
            normal_price TEXT,
            night_price TEXT,
            overnight_price TEXT,
            package_price TEXT,
            member_price TEXT,
            newcustomer_offer TEXT,
            oldcustomer_offer TEXT,
            recharge_promo TEXT,
            tournament_fee TEXT,
            drink_price TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- M3: 客流观察
        CREATE TABLE IF NOT EXISTS ci_traffic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            obs_date TEXT NOT NULL,
            time_slot TEXT,
            observed_tables INTEGER DEFAULT 0,
            active_players INTEGER DEFAULT 0,
            is_full INTEGER DEFAULT 0,
            is_queuing INTEGER DEFAULT 0,
            activity_level TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- M4: 玩家画像
        CREATE TABLE IF NOT EXISTS ci_player_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            player_type TEXT,
            active_time TEXT,
            spending_level TEXT,
            can_bring_guests INTEGER DEFAULT 0,
            description TEXT,
            estimated_count INTEGER,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- M5: 核心玩家生态
        CREATE TABLE IF NOT EXISTS ci_key_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            anonymous_id TEXT,
            active_frequency TEXT,
            usual_group_size INTEGER,
            skill_level TEXT,
            spending_power TEXT,
            social_influence TEXT,
            conversion_value TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- M6: 服务体验评分
        CREATE TABLE IF NOT EXISTS ci_service_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            score_date TEXT NOT NULL,
            env_score INTEGER,
            cleanliness_score INTEGER,
            ac_air_score INTEGER,
            seat_score INTEGER,
            staff_attitude_score INTEGER,
            response_speed_score INTEGER,
            newcomer_friendly_score INTEGER,
            regular_maintain_score INTEGER,
            community_atmosphere_score INTEGER,
            overall_score INTEGER,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- M7: 微信群生态
        CREATE TABLE IF NOT EXISTS ci_community (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            record_date TEXT NOT NULL,
            group_size INTEGER,
            active_members INTEGER,
            daily_messages INTEGER,
            activity_frequency TEXT,
            newcomer_mechanism TEXT,
            tournament_org TEXT,
            admin_activity TEXT,
            group_culture TEXT,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- M8: 营销活动
        CREATE TABLE IF NOT EXISTS ci_marketing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            competitor_id INTEGER NOT NULL,
            activity_date TEXT NOT NULL,
            activity_type TEXT,
            content TEXT,
            promotion_channel TEXT,
            estimated_cost REAL,
            observed_effect TEXT,
            worth_learning INTEGER DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            FOREIGN KEY (competitor_id) REFERENCES ci_competitors(id)
        );

        -- ===== 运营任务表 (Operation Intelligence Layer V1.1) =====
        CREATE TABLE IF NOT EXISTS operation_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            task_type TEXT NOT NULL,
            priority TEXT DEFAULT 'normal',
            description TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            completed_at TEXT,
            operator TEXT,
            FOREIGN KEY (player_id) REFERENCES players(id)
        );

        -- ===== V1.2 玩家关系网络 =====
        CREATE TABLE IF NOT EXISTS player_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_a_id INTEGER NOT NULL,
            player_b_id INTEGER NOT NULL,
            relationship_type TEXT DEFAULT 'neutral',   -- positive/neutral/avoid
            relationship_score INTEGER DEFAULT 0,       -- -100 到 100
            note TEXT,
            source TEXT DEFAULT 'manual',               -- manual(人工) / auto(自动建议)
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY (player_a_id) REFERENCES players(id),
            FOREIGN KEY (player_b_id) REFERENCES players(id)
        );

        -- ===== V1.3 经营反馈闭环 + 桌局复盘 =====

        -- 桌局反馈（每桌一次，牌局结束后填写）
        CREATE TABLE IF NOT EXISTS session_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            atmosphere_score INTEGER DEFAULT 3,      -- 桌面氛围 1-5
            compatibility_score INTEGER DEFAULT 3,  -- 玩家匹配程度 1-5
            table_quality_score INTEGER DEFAULT 3,  -- 整体质量 1-5
            conflict_level INTEGER DEFAULT 1,       -- 冲突程度 1-5
            conflict_type TEXT DEFAULT 'none',      -- none/skill_gap/personality/money_pressure/other
            notes TEXT,
            operator TEXT,
            created_at TEXT,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        );

        -- 玩家组合效果统计（基于 session_players + session_feedback 学习）
        CREATE TABLE IF NOT EXISTS player_pair_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_a_id INTEGER NOT NULL,
            player_b_id INTEGER NOT NULL,
            play_count INTEGER DEFAULT 0,
            positive_count INTEGER DEFAULT 0,
            negative_count INTEGER DEFAULT 0,
            average_score REAL DEFAULT 0,
            last_play_date TEXT,
            relationship_trend TEXT DEFAULT 'unknown',  -- improving/declining/stable/unknown
            updated_at TEXT,
            FOREIGN KEY (player_a_id) REFERENCES players(id),
            FOREIGN KEY (player_b_id) REFERENCES players(id)
        );

        -- 每日经营快照（长期经营分析用）
        CREATE TABLE IF NOT EXISTS daily_operation_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            gmv REAL DEFAULT 0,
            sessions INTEGER DEFAULT 0,
            players INTEGER DEFAULT 0,
            new_players INTEGER DEFAULT 0,
            repeat_players INTEGER DEFAULT 0,
            average_table_score REAL DEFAULT 0,
            task_completion_rate REAL DEFAULT 0,
            created_at TEXT
        );

        -- ===== 支付流水（扫码收款确认到账）=====
        -- 每笔扫码支付在网关确认后写入，结账时强制校验 status=SUCCESS 且金额一致
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            out_trade_no TEXT UNIQUE NOT NULL,
            auth_code_mask TEXT,
            method TEXT,
            amount REAL,
            status TEXT,
            transaction_id TEXT,
            provider TEXT,
            session_player_id INTEGER,
            created_at TEXT,
            updated_at TEXT
        );
    ''')

    # ===== 迁移：给Phase 1的session_players表添加player_id列 =====
    cols = db.execute('PRAGMA table_info(session_players)').fetchall()
    col_names = [c['name'] for c in cols]
    if 'player_id' not in col_names:
        db.execute('ALTER TABLE session_players ADD COLUMN player_id INTEGER')

    # ===== 迁移：session_players 加单人计费字段 =====
    sp_new_cols = {
        'start_time': 'TEXT',
        'end_time': 'TEXT',
        'duration_minutes': 'INTEGER',
        'fee': 'REAL',
        'fee_breakdown': 'TEXT',
        'discount_type': 'TEXT',
        'discount_id': 'INTEGER',
        'discount_amount': 'REAL DEFAULT 0',
        'final_fee': 'REAL',
        'product_total': 'REAL DEFAULT 0',
        'grand_total': 'REAL',
        'payment_method': 'TEXT',
        'status': "TEXT DEFAULT 'playing'",
    }
    for col_name, col_type in sp_new_cols.items():
        if col_name not in col_names:
            db.execute(f'ALTER TABLE session_players ADD COLUMN {col_name} {col_type}')

    # ===== 迁移：session_players 加 is_overnight 列 =====
    if 'is_overnight' not in col_names:
        db.execute('ALTER TABLE session_players ADD COLUMN is_overnight INTEGER DEFAULT 0')

    # ===== 迁移：session_players 加手动台费折扣字段 =====
    sp_manual_discount_cols = {
        'manual_discount_type': 'TEXT',
        'manual_discount_value': 'REAL DEFAULT 0',
        'manual_discount_amount': 'REAL DEFAULT 0',
    }
    for col_name, col_type in sp_manual_discount_cols.items():
        if col_name not in col_names:
            db.execute(f'ALTER TABLE session_players ADD COLUMN {col_name} {col_type}')

    # ===== 迁移：product_sales 支持无码商品 =====
    # SQLite 不能直接删除 NOT NULL，需要重建表
    ps_cols = db.execute('PRAGMA table_info(product_sales)').fetchall()
    ps_col_names = [c['name'] for c in ps_cols]
    has_category = 'category' in ps_col_names
    has_is_custom = 'is_custom' in ps_col_names
    has_custom_category = 'custom_category' in ps_col_names
    product_id_not_null = any(c['name'] == 'product_id' and c['notnull'] == 1 for c in ps_cols)
    has_old_backup = 'product_sales_old' in [t['name'] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]

    if product_id_not_null or not has_category or not has_is_custom or not has_custom_category or has_old_backup:
        old_rows = []
        if has_old_backup:
            old_rows = db.execute('SELECT * FROM product_sales_old').fetchall()
            db.execute('DROP TABLE product_sales_old')
        elif 'product_sales' in [t['name'] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
            old_rows = db.execute('SELECT * FROM product_sales').fetchall()
            db.execute('ALTER TABLE product_sales RENAME TO product_sales_old')
            old_rows = db.execute('SELECT * FROM product_sales_old').fetchall()

        # 创建新表
        db.execute('''
            CREATE TABLE product_sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                session_player_id INTEGER,
                product_id INTEGER,
                product_name TEXT NOT NULL,
                category TEXT,
                price REAL NOT NULL,
                quantity INTEGER DEFAULT 1,
                total REAL NOT NULL,
                is_custom INTEGER DEFAULT 0,
                custom_category TEXT,
                payment_method TEXT,
                member_id INTEGER,
                created_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (product_id) REFERENCES products(id),
                FOREIGN KEY (member_id) REFERENCES members(id)
            )
        ''')

        # 复制数据：逐行插入，缺失字段用默认值
        for r in old_rows:
            r = dict(r)
            db.execute('''
                INSERT INTO product_sales (session_id, session_player_id, product_id, product_name, category, price, quantity, total, is_custom, custom_category, payment_method, member_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', [
                r.get('session_id'),
                r.get('session_player_id'),
                r.get('product_id'),
                r.get('product_name'),
                r.get('category'),
                r.get('price'),
                r.get('quantity', 1),
                r.get('total'),
                r.get('is_custom', 0),
                r.get('custom_category'),
                r.get('payment_method'),
                r.get('member_id'),
                r.get('created_at')
            ])
        # 删除旧备份
        if 'product_sales_old' in [t['name'] for t in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
            db.execute('DROP TABLE product_sales_old')

    # ===== 迁移：product_sales 加玩家绑定 + 结算状态 =====
    # 游戏中挂账到玩家场次 -> UNSETTLED；柜台即时售卖 -> SETTLED。
    # 该结构用于"换店员不丢消费"与"防重复结算"。
    ps_cols2 = db.execute('PRAGMA table_info(product_sales)').fetchall()
    ps_col_names2 = [c['name'] for c in ps_cols2]
    for col_name, col_type in [
        ('player_id', 'INTEGER'),
        ('status', "TEXT DEFAULT 'UNSETTLED'"),
        ('settled_at', 'TEXT'),
    ]:
        if col_name not in ps_col_names2:
            db.execute(f'ALTER TABLE product_sales ADD COLUMN {col_name} {col_type}')

    p_cols = db.execute('PRAGMA table_info(players)').fetchall()
    p_col_names = [c['name'] for c in p_cols]
    new_player_cols = {
        # 身份与联系
        'real_name': 'TEXT',
        'preferred_name': 'TEXT',
        'gender': 'TEXT',
        'birthday': 'TEXT',
        'wechat_remark': 'TEXT',
        'area': 'TEXT',
        'occupation': 'TEXT',
        'industry': 'TEXT',
        # 来源与关系
        'source_channel': 'TEXT',
        'introducer': 'TEXT',
        'relationship_strength': 'TEXT',
        'personality_tags': 'TEXT',
        # 技术与偏好
        'player_type': 'TEXT',
        'skill_level': 'TEXT',
        'preferred_mode': 'TEXT',
        'preferred_time': 'TEXT',
        'can_overnight': 'TEXT',
        'tournament_interest': 'TEXT',
        # 组织者
        'organizer_candidate': 'TEXT',
        'organizer_level': 'TEXT',
        'organizer_note': 'TEXT',
        # 行为汇总 (从组局记录自动计算或快照)
        'last_visit': 'TEXT',
        'total_visits': 'INTEGER DEFAULT 0',
        'visits_30d': 'INTEGER DEFAULT 0',
        'activity_level': 'TEXT',
        'common_mode': 'TEXT',
        'active_behavior': 'TEXT',
        'is_organizer': 'INTEGER DEFAULT 0',
        # CRM与营销
        'maintenance_priority': 'TEXT',
        'marketing_tags': 'TEXT',
        'risk_tags': 'TEXT',
        'follow_up_status': 'TEXT',
        'next_follow_up': 'TEXT',
        'last_contact': 'TEXT',
        'last_contact_summary': 'TEXT',
        # 偏好
        'drink_preference': 'TEXT',
        'price_sensitivity': 'TEXT',
        # 元数据
        'profile_completeness': 'TEXT',
        'customer_score': 'REAL',
        'customer_level': 'TEXT',
        'customer_score_updated': 'TEXT',
        # ===== V1.2 玩家关系网络 + 智能组局 =====
        'initiative_level': 'TEXT',       # 主动性: 主动型/semi_active/被动型/unknown
        'initiative_score': 'REAL',       # 主动性评分 0-100
        'initiative_updated': 'TEXT',     # 评分更新时间
        'table_style_preference': 'TEXT', # 局型偏好: competitive/entertainment/social/high_variance/unknown
        # ===== V1.3 经营反馈闭环 + 桌局复盘 =====
        'experience_score': 'REAL',          # 体验评分 0-100（正向桌局提升/负向下降）
        'compatibility_score': 'REAL',       # 适配评分 0-100（与他人的兼容度）
        'conflict_count': 'INTEGER DEFAULT 0',       # 冲突桌次数
        'positive_table_count': 'INTEGER DEFAULT 0', # 正向桌局次数
        'negative_table_count': 'INTEGER DEFAULT 0', # 负向桌局次数
    }
    for col_name, col_type in new_player_cols.items():
        if col_name not in p_col_names:
            db.execute(f'ALTER TABLE players ADD COLUMN {col_name} {col_type}')

    # ===== 迁移：players 归档/停用支持 =====
    # status: active / archived；归档不删除任何历史数据。
    p_cols3 = db.execute('PRAGMA table_info(players)').fetchall()
    p_col_names3 = [c['name'] for c in p_cols3]
    for col_name, col_type in [
        ('status', "TEXT DEFAULT 'active'"),
        ('archived_at', 'TEXT'),
        ('archive_reason', 'TEXT'),
    ]:
        if col_name not in p_col_names3:
            db.execute(f'ALTER TABLE players ADD COLUMN {col_name} {col_type}')

    # ===== 迁移：ci_competitors 加运营信息字段 =====
    cc_cols = db.execute('PRAGMA table_info(ci_competitors)').fetchall()
    cc_col_names = [c['name'] for c in cc_cols]
    cc_new_cols = {
        'business_hours': 'TEXT',      # 营业时间
        'operating_status': 'TEXT',    # 运营状态 active/preparing/closed
        'contact': 'TEXT',             # 联系方式（公开信息）
        'notes': 'TEXT',               # 备注
    }
    for col_name, col_type in cc_new_cols.items():
        if col_name not in cc_col_names:
            db.execute(f'ALTER TABLE ci_competitors ADD COLUMN {col_name} {col_type}')

    # ===== 迁移：members 加手机号和消费密码字段 =====
    m_cols = db.execute('PRAGMA table_info(members)').fetchall()
    m_col_names = [c['name'] for c in m_cols]
    member_new_cols = {
        'phone': 'TEXT',
        'pin_hash': 'TEXT',
    }
    for col_name, col_type in member_new_cols.items():
        if col_name not in m_col_names:
            db.execute(f'ALTER TABLE members ADD COLUMN {col_name} {col_type}')

    # ===== 迁移：visit_records 加 真实支付金额 字段（来自 Excel 组局记录） =====
    vr_cols = db.execute('PRAGMA table_info(visit_records)').fetchall()
    vr_col_names = [c['name'] for c in vr_cols]
    if 'payment_amount' not in vr_col_names:
        db.execute('ALTER TABLE visit_records ADD COLUMN payment_amount REAL DEFAULT 0')

    # ===== 迁移：visit_records 加关联 session_id，便于从 Excel 回查组局 =====
    if 'session_id' not in vr_col_names:
        db.execute('ALTER TABLE visit_records ADD COLUMN session_id INTEGER')

    # ===== 迁移：sessions 加 source_id，防止 Excel 组局记录重复导入 =====
    s_cols = db.execute('PRAGMA table_info(sessions)').fetchall()
    s_col_names = [c['name'] for c in s_cols]
    if 'source_id' not in s_col_names:
        db.execute('ALTER TABLE sessions ADD COLUMN source_id TEXT')

    # ===== 迁移：sessions 加历史导入元数据 =====
    # time_precision: date_only 表示时间来自 Excel 仅精确到日期（不伪造具体时刻）
    # import_quality: PARTIAL 表示不完整桌（已知玩家 < 4）
    if 'time_precision' not in s_col_names:
        db.execute('ALTER TABLE sessions ADD COLUMN time_precision TEXT')
    if 'import_quality' not in s_col_names:
        db.execute('ALTER TABLE sessions ADD COLUMN import_quality TEXT')

    # ===== 迁移：legacy_import_records 导入登记表 =====
    # 防止同一 Excel 行重复导入；UNIQUE(source, source_sheet, source_row) 为幂等核心。
    db.execute(
        '''CREATE TABLE IF NOT EXISTS legacy_import_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_sheet TEXT,
            source_row INTEGER,
            legacy_table_no TEXT,
            date TEXT,
            entity_type TEXT,
            entity_id INTEGER,
            imported_at TEXT,
            hash TEXT,
            UNIQUE(source, source_sheet, source_row)
        )'''
    )

    # 插入默认机器
    for m in MACHINES:
        existing = db.execute('SELECT id FROM machines WHERE id=?', [m['id']]).fetchone()
        if not existing:
            db.execute(
                'INSERT INTO machines (id, name, type, status, sort_order) VALUES (?, ?, ?, "idle", ?)',
                [m['id'], m['name'], m['type'], m['sort_order']]
            )

    # 插入默认设置
    for key, value in DEFAULT_SETTINGS.items():
        existing = db.execute('SELECT key FROM settings WHERE key=?', [key]).fetchone()
        if not existing:
            db.execute('INSERT INTO settings (key, value) VALUES (?, ?)', [key, str(value)])

    # 插入默认 admin 用户（仅当不存在时；绝不修改已有管理员密码）
    # 默认密码不再硬编码 admin123，改由环境变量 QCOS_ADMIN_PASSWORD 提供，
    # 缺失时生成一次性随机密码（详见 _ensure_default_admin）。
    admin = db.execute('SELECT id FROM users WHERE username=?', ['admin']).fetchone()
    if not admin:
        _ensure_default_admin(db)

    # 插入默认商品
    for p in DEFAULT_PRODUCTS:
        existing = db.execute('SELECT id FROM products WHERE name=? AND category=?', [p['name'], p['category']]).fetchone()
        if not existing:
            db.execute(
                'INSERT INTO products (name, category, price, cost, stock, is_active, sort_order, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, datetime("now"))',
                [p['name'], p['category'], p['price'], p.get('cost', 0), p.get('stock', -1), p.get('sort_order', 0)]
            )

    db.commit()
    db.close()


def get_all_settings(db):
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    settings = {}
    for row in rows:
        val = row['value']
        try:
            settings[row['key']] = int(val)
        except (ValueError, TypeError):
            try:
                settings[row['key']] = float(val)
            except (ValueError, TypeError):
                settings[row['key']] = val
    return settings


def update_setting(db, key, value):
    db.execute(
        'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)',
        [key, str(value)]
    )
    db.commit()
