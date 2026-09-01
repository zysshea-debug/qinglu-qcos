"""QCOS pytest 测试基础设施（Final Hardening，最小修复）。

目标：`pytest tests/` 真实收集并运行所有测试，不靠 collect_ignore 隐藏；
每测试独立临时库，config.DB_PATH / models.DB_PATH / config.PAYMENT 均保存并恢复，
杜绝 import 阶段全局 config 污染在单进程多套件间泄漏。

仅做测试基础设施，不修改生产鉴权 / revenue / cashier 逻辑：
- 不绕过 login_required / 不判断 TESTING 跳过登录；
- 不读取 .env、不使用真实 qcos.db（测试库均为临时库）。
"""
import os
import copy
import tempfile

import pytest

# 测试专用管理员密码：必须在 import config 之前设置，使临时库 admin 仅使用测试密码，
# 且各测试模块 login() 硬编码 'admin123' 能与 init_db 创建的 admin 匹配。
# 不读取 .env、不触碰真实 qcos.db。
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'admin123')
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')

import config as _cfg

# 捕获生产默认支付配置（在任何测试模块 import 之前），用于每测试恢复，
# 杜绝 import 期改写的 config.PAYMENT['provider'] 泄漏到其它套件。
_ORIGINAL_PAYMENT = copy.deepcopy(_cfg.PAYMENT)

import tempfile as _tf

# 防御：在测试模块 import app / models 之前把全局 DB_PATH 指向临时库，
# 避免 app.py 模块级 init_db() 在收集阶段于仓库内创建 / 迁移真实 qcos.db
# （生产 qcos.db 始终只读，测试库均为临时库）。
_cfg.DB_PATH = _tf.mktemp(suffix='.db')


@pytest.fixture(autouse=True, scope='function')
def _isolate_db_per_test(request):
    """每测试独立临时库：覆盖 config.DB_PATH / models.DB_PATH 并建表，
    测试结束后还原旧值并删除临时库 —— 解决同进程多套件 DB_PATH 污染。
    同时把 config.PAYMENT 恢复为生产默认，隔离支付配置污染。

    关键：部分套件（test_history_cashier_compat / test_analytics /
    test_operation_feedback）在 import 期创建了「模块级 DB = get_db()」连接，
    该连接已绑定到 import 期的旧 DB_PATH。本 fixture 在重定向 DB_PATH 后，
    把模块级 DB 重新指向当前临时库，使直接 DB 查询与 API（models.get_db）
    写入落到同一个库，避免「直写旧库、API 读新库」导致行不可见。
    """
    import config
    import models
    import sqlite3

    old_cfg = config.DB_PATH
    old_mod = models.DB_PATH
    old_pay = copy.deepcopy(config.PAYMENT)
    tmp = tempfile.mktemp(suffix='.db')
    config.DB_PATH = tmp
    models.DB_PATH = tmp
    models.init_db()  # 建表 + 默认 admin（密码 = 测试专用值，非真实库）
    config.PAYMENT = copy.deepcopy(_ORIGINAL_PAYMENT)
    # 重新绑定模块级 DB 连接到当前临时库（仅当其为 sqlite 连接时）
    # 仅对显式声明 _REBIND_DB_PER_TEST 的模块生效：这类模块在每测试内用模块级
    # DB 直写、并依赖 API 读取同一库（如 test_history_cashier_compat 配合
    # _clean_before_each）。对「import 期一次性造数、测试函数再直查 DB」的模块
    # （如 test_analytics），重新绑定会把 DB 指到空的全新临时库，反而破坏测试，
    # 故需显式 opt-in，避免误伤。
    mod = getattr(request, 'module', None)
    if mod is not None and getattr(mod, '_REBIND_DB_PER_TEST', False):
        existing = getattr(mod, 'DB', None)
        if isinstance(existing, sqlite3.Connection):
            try:
                existing.close()
            except Exception:
                pass
            mod.DB = models.get_db()
    try:
        yield
    finally:
        config.DB_PATH = old_cfg
        models.DB_PATH = old_mod
        config.PAYMENT = old_pay
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


@pytest.fixture(autouse=True, scope='function')
def _logged_in_client(request, _isolate_db_per_test):
    """对提供模块级 client + login() 的套件自动登录（依赖 DB 隔离先完成），
    使其依赖鉴权的接口在 pytest 下与脚本直跑行为一致。"""
    module = request.module
    client = getattr(module, 'client', None)
    login = getattr(module, 'login', None)
    if client is not None and callable(login):
        login()
    return client


@pytest.fixture(scope='function')
def S(request, _isolate_db_per_test):
    """兼容显式依赖 S 的文件（test_cashier_history / test_history_cashier_compat /
    test_in_progress_estimate 等）：S 为模块级 seed() 返回的种子数据字典。
    每测试重新 seed 到隔离库，保证数据存在。非此类文件不依赖本 fixture。
    """
    mod = request.module
    if not hasattr(mod, 'seed'):
        pytest.fail(f'{mod.__name__} 未定义 seed()，S fixture 不适用')
    return mod.seed()


# 脚本式套件普遍用 check()/bad()「记录失败」而非抛断言，若只看 pytest 结果会漏判。
# 这里在每次测试后比对模块级失败计数器，只要本次测试记录过失败就判定该测试不通过，
# 保证 pytest 的全绿与脚本直跑的 EXIT=0 含义一致。
_FAILURE_COUNTERS = ('FAIL', 'failed', '_fail')


@pytest.fixture(autouse=True, scope='function')
def _fail_on_recorded_failure(request):
    module = request.module
    names = [n for n in _FAILURE_COUNTERS if isinstance(getattr(module, n, None), int)]
    before = {n: getattr(module, n) for n in names}
    yield
    for n in names:
        delta = getattr(module, n) - before[n]
        if delta > 0:
            pytest.fail(f'{module.__name__} 本次测试记录了 {delta} 项失败（计数器 {n}）', pytrace=False)
