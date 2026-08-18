"""青鹭收银系统 - 配置文件"""

import os

# 台桌配置
MACHINES = [
    {'id': 1, 'name': '八口机1', 'type': '8port', 'sort_order': 1},
    {'id': 2, 'name': '八口机2', 'type': '8port', 'sort_order': 2},
    {'id': 3, 'name': '四口机1', 'type': '4port', 'sort_order': 3},
    {'id': 4, 'name': '四口机2', 'type': '4port', 'sort_order': 4},
]

MACHINE_TYPE_LABELS = {
    '8port': '八口机',
    '4port': '四口机',
}

MACHINE_MAX_PLAYERS = {
    '8port': 8,
    '4port': 4,
}

# 默认设置（初始化时写入数据库，之后从UI修改）
DEFAULT_SETTINGS = {
    'rate_8port': 20,
    'rate_4port': 10,
    'overnight_8port': 100,
    'overnight_4port': 50,
    'selfservice_multiplier': 0.5,
    'rounding': 'none',
    # ===== V1.4 计费体验优化（仅作用于正常小时费率，不适用于通宵包夜）=====
    'billing_buffer_minutes': 10,      # 每小时结束后的免费缓冲分钟数
    'billing_step_minutes': 10,        # 额外时间计费单位（分钟）
    'billing_rounding_mode': 'buffer_step',  # buffer_step(默认) / hour_round / exact_minute
    'lottery_free_count': 1,
    'lottery_free_max': 100,
    'lottery_half_count': 2,
    'lottery_half_max': 50,
    'lottery_discount_count': 12,
    'lottery_discount_max': 50,
    'monthly_target_gmv': 30000,
}

LOTTERY_TYPES = {
    'free': {'label': '免单', 'default_max': 100},
    'half': {'label': '半价', 'default_max': 50},
    'discount': {'label': '八折', 'default_max': 50},
}

PAYMENT_METHODS = {
    'scan_wechat': '微信扫码',
    'scan_alipay': '支付宝扫码',
    'wechat': '微信',
    'alipay': '支付宝',
    'cash': '现金',
    'member': '会员余额',
}

# ===== 支付确认（扫码收款必须确认到账）=====
# provider: None(沿用旧逻辑，仅靠收银员点确认，不确认到账)
#           'mock'(测试用，模拟成功)
#           'zhonglun'(四川中仑数科科技有限公司 开放平台)
# 注意：设为 'zhonglun' 前，必须先填好下方 zhonglun 的各凭证与 action 名，
# 否则扫码结账会被系统拒绝（这正是「必须确认到账」的约束）。
PAYMENT = {
    'provider': None,
    'zhonglun': {
        'api_base': 'https://open.zhonglunnet.com',
        'key': 'zl.open.pay.micro',        # TODO: 中仑开放平台具体 key，见其文档
        'version': 'v5',                   # TODO: 中仑开放平台接口版本
        # 真实凭证请勿硬编码到代码中，统一通过环境变量 / .env 注入（.env 已被 .gitignore 忽略）
        'appkey': os.environ.get('QCOS_ZHONGLUN_APPKEY', ''),   # 中仑开放平台「应用编码」
        'secret': os.environ.get('QCOS_ZHONGLUN_SECRET', ''),   # 中仑开放平台「应用秘钥」
        'shop_auth_code': '',              # 门店授权码
        'micropay_action': '',             # TODO: 扫码收款/被扫 action 名（文档中心>API文档>支付）
        'query_action': '',                # TODO: 订单查询 action 名
        'notify_action': '',               # TODO: 异步通知 action 名（若开放平台推送给我方）
    },
}

# 角色权限
ROLES = {
    'admin': {'label': '管理员', 'permissions': ['*']},
    'staff': {'label': '店员', 'permissions': ['dashboard', 'lottery', 'daily', 'products', 'players', 'members', 'checkout', 'table_matcher', 'session_feedback', 'operation_review', 'analytics']},
    'viewer': {'label': '只读', 'permissions': ['dashboard', 'daily', 'players', 'members']},
}

# 角色对应的页面访问权限
PAGE_PERMISSIONS = {
    'dashboard': ['admin', 'staff', 'viewer'],
    'lottery': ['admin', 'staff'],
    'daily': ['admin', 'staff', 'viewer'],
    'products': ['admin', 'staff'],
    'players': ['admin', 'staff', 'viewer'],
    'members': ['admin', 'staff', 'viewer'],
    'staff_mgmt': ['admin', 'staff'],
    'competition': ['admin', 'staff', 'viewer'],
    'operations': ['admin', 'staff', 'viewer'],
    'staff_tasks': ['admin', 'staff'],
    'table_matcher': ['admin', 'staff'],
    'session_feedback': ['admin', 'staff'],
    'operation_review': ['admin', 'staff'],
    'analytics': ['admin', 'staff', 'viewer'],
    'settings': ['admin'],
    'users': ['admin'],
}

# 商品分类
PRODUCT_CATEGORIES = {
    'drink': '饮料',
    'snack': '零食',
    'other': '其他',
}

# 场务类型
STAFF_TYPES = {
    'entertainment': '娱乐场务',
    'competitive': '竞技场务',
}

# 场务状态
STAFF_STATUS = {
    'active': '在职',
    'inactive': '离职',
}

# 结算状态
SETTLEMENT_STATUS = {
    'pending': '待发放',
    'paid': '已发放',
}

# ===== 竞争情报系统 =====

# 客流观察时间段
CI_TIME_SLOTS = {
    'afternoon': '下午 14:00-18:00',
    'evening': '晚上 18:00-22:00',
    'night': '夜间 22:00-02:00',
    'late_night': '凌晨 02:00以后',
}

# 玩家类型
CI_PLAYER_TYPES = {
    'competitive': '竞技高手',
    'casual': '娱乐玩家',
    'beginner': '新手玩家',
    'student': '学生',
    'white_collar': '白领',
    'female': '女玩家',
    'fixed_group': '固定牌友团',
    'high_freq': '高频玩家',
}

# 活跃程度
CI_ACTIVITY_LEVELS = {
    'high': '高（基本满桌）',
    'medium': '中（半数上座）',
    'low': '低（少量玩家）',
    'empty': '空（无人）',
}

# 消费能力等级
CI_SPENDING_LEVELS = {
    'high': '高',
    'medium': '中',
    'low': '低',
}

# 社交影响力
CI_SOCIAL_INFLUENCE = {
    'high': '高（带动力强）',
    'medium': '中',
    'low': '低',
}

# 活跃频率
CI_FREQ_LEVELS = {
    'daily': '几乎每天',
    'weekly': '每周多次',
    'biweekly': '每两周',
    'monthly': '每月',
    'rare': '偶尔',
}

# 技术水平
CI_SKILL_LEVELS = {
    'expert': '高手',
    'intermediate': '中等',
    'beginner': '新手',
}

# 服务评分维度
CI_SCORE_DIMENSIONS = [
    ('env_score', '环境'),
    ('cleanliness_score', '卫生'),
    ('ac_air_score', '空调空气'),
    ('seat_score', '座椅舒适度'),
    ('staff_attitude_score', '店员态度'),
    ('response_speed_score', '回复速度'),
    ('newcomer_friendly_score', '新人友好度'),
    ('regular_maintain_score', '老客维护'),
    ('community_atmosphere_score', '社群氛围'),
    ('overall_score', '整体体验'),
]

# 竞争评分模型权重 (总分100)
CI_SCORE_WEIGHTS = {
    'traffic': 20,        # 客流
    'key_players': 15,    # 核心玩家
    'price': 15,          # 价格竞争力
    'environment': 15,    # 环境体验
    'community': 15,      # 社群运营
    'service': 10,        # 服务
    'brand': 10,          # 品牌影响力
}

CI_SCORE_DIM_LABELS = {
    'traffic': '客流',
    'key_players': '核心玩家',
    'price': '价格竞争力',
    'environment': '环境体验',
    'community': '社群运营',
    'service': '服务',
    'brand': '品牌影响力',
}

# 营销活动类型
CI_MARKETING_TYPES = {
    'tournament': '比赛',
    'discount': '优惠',
    'recharge': '充值活动',
    'newcomer': '新人活动',
    'social': '社群活动',
    'other': '其他',
}

# 竞争店运营状态
CI_OPERATING_STATUS = {
    'active': '营业中',
    'preparing': '筹备中',
    'closed': '已停业',
}

# 默认商品
DEFAULT_PRODUCTS = [
    {'name': '矿泉水', 'category': 'drink', 'price': 3, 'cost': 1, 'sort_order': 1},
    {'name': '可乐', 'category': 'drink', 'price': 5, 'cost': 3, 'sort_order': 2},
    {'name': '雪碧', 'category': 'drink', 'price': 5, 'cost': 3, 'sort_order': 3},
    {'name': '红牛', 'category': 'drink', 'price': 8, 'cost': 5, 'sort_order': 4},
    {'name': '王老吉', 'category': 'drink', 'price': 6, 'cost': 4, 'sort_order': 5},
    {'name': '东方树叶', 'category': 'drink', 'price': 5, 'cost': 3, 'sort_order': 6},
    {'name': '乐事薯片', 'category': 'snack', 'price': 8, 'cost': 5, 'sort_order': 10},
    {'name': '卫龙辣条', 'category': 'snack', 'price': 5, 'cost': 3, 'sort_order': 11},
    {'name': '火腿肠', 'category': 'snack', 'price': 3, 'cost': 2, 'sort_order': 12},
    {'name': '泡面', 'category': 'snack', 'price': 6, 'cost': 4, 'sort_order': 13},
]

def _load_dotenv():
    """从项目根目录的 .env 文件加载环境变量（若存在）。
    仅在对应变量尚未在环境中设置时才赋值，避免覆盖已显式设置的值。
    用于本地 / 门店部署时注入 QCOS_SECRET_KEY、QCOS_ADMIN_PASSWORD 等敏感配置，
    且 .env 已被 .gitignore 忽略，不会进入 Git。不依赖第三方库。
    """
    dotenv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
    if not os.path.exists(dotenv_path):
        return
    with open(dotenv_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, _, value = line.partition('=')
            key, value = key.strip(), value.strip()
            if not key:
                continue
            # 去掉可选的引号包裹
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)


_load_dotenv()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qcos.db')

# ===== 安全约束：SECRET_KEY 必须来自环境变量，禁止硬编码真实密钥 =====
# 生产环境若未设置 QCOS_SECRET_KEY，直接报错退出，绝不允许回退到任何默认密钥。
SECRET_KEY = os.environ.get('QCOS_SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError(
        "安全错误：未设置环境变量 QCOS_SECRET_KEY，已拒绝使用硬编码默认密钥。\n"
        "请在部署环境（.env 或系统环境变量）中设置强随机密钥后启动，例如：\n"
        "    QCOS_SECRET_KEY=$(python -c \"import secrets;print(secrets.token_hex(32))\")\n"
        "并将该值写入项目根目录的 .env 文件（参考 .env.example，.env 已被 .gitignore 忽略，不会进入 Git）。"
    )
