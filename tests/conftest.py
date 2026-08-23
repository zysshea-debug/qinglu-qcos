"""QCOS pytest 测试鉴权脚手架（最小修复，仅测试基础设施）

背景（审计确认）：
- 11 个 pytest 风格测试执行结果为 53 passed / 38 failed / 7 errors，
  失败集中于「401 请先登录」与「no such table」。
- 根因一（鉴权）：各测试模块在 pytest 模式下不会执行 `if __name__ == '__main__':` 里的
  login()，受保护接口统一返回 401。
- 根因二（库绑定）：models.py 经 `from config import DB_PATH` 在首次导入时捕获路径
  （stale binding）。pytest 单进程内多个模块各自 `import models` 被缓存，
  后续模块的临时库从未真正建表（no such table），而 app 请求落到首个模块的库（无登录态 → 401）。
- 根因三（收集崩溃）：tests/ 下存在多个脚本式测试文件（无 test_ 函数、无 __main__ 守卫），
  pytest 导入即执行模块级代码，sys.exit() 直接导致整个测试会话 INTERNALERROR。
  这些文件以 `python tests/test_x.py` 方式直接运行（见下方 collect_ignore 注释）。

本 conftest 只修测试基础设施，不触碰生产鉴权 / GMV / revenue / cashier 逻辑：
1. collect_ignore：把脚本式测试文件排除出 pytest 收集范围（它们按设计用 python 直跑）。
2. module 级 autouse fixture：为需要登录的测试模块建立独立临时库
   （修复 stale binding + 建表 + 默认 admin），并经正常 /api/auth/login 流程以
   「测试库 admin」登录模块级 client，使受保护接口测试具备合法登录态。
3. 仅登录「模块级 client」；匿名 / 权限专项测试使用独立 client（如 test_overnight_auto_end
   的 client_anon）或自行 logout/login（如 test_player_archive_delete 的 staff 权限用例），
   不会被自动登录掩盖 → 权限语义保留。可用 @pytest.mark.no_auto_login 显式跳过。
4. S fixture：兼容显式依赖 S 的 test_cashier_history —— S 的真实语义是模块级 seed()
   返回的种子数据字典（测试通过 S['fee_a'] / S['sid_b'] 引用预期值），并非登录态/客户端。

安全约束（本文件绝不触碰）：
- 不修改生产 login_required / 不绕过鉴权 / 不判断 TESTING 跳过登录。
- 不硬编码真实生产管理员密码：此处强制 QCOS_ADMIN_PASSWORD 为测试专用值，
  使临时库 admin 只可能使用测试密码；不读取 .env、不使用真实 qcos.db。
"""

import os
import tempfile

import pytest

# ===== 测试专用环境变量（在测试模块 import config 之前生效）=====
# 强制覆盖：保证临时库 admin 密码只可能是测试值，杜绝继承任何真实密码。
# pytest 先导入本文件，再导入各测试模块，因此此处赋值先于模块级 setdefault。
os.environ['QCOS_ADMIN_PASSWORD'] = 'test_admin_pw_qcos_v2'
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')

# ===== pytest 收集范围：排除脚本式测试文件 =====
# 这些文件无 test_ 函数，设计上以 `python tests/test_x.py` 直接运行
# （脚本式回归）。若被 pytest 导入会执行模块级代码（甚至 sys.exit()），
# 导致收集 INTERNALERROR / 状态污染。故从 pytest 收集范围排除。
collect_ignore = [
    'test_august_2026_history_import.py',  # 脚本式（有 __main__ 守卫，python 直跑）
    'test_billing_experience.py',          # 脚本式（无 __main__ 守卫，模块级 sys.exit）
    'test_operation_feedback.py',          # 脚本式（无 __main__ 守卫，模块级直接执行）
    'test_operations.py',                  # 脚本式（无 __main__ 守卫，模块级 sys.exit）
    'test_overnight_auto_end.py',          # 脚本式（无 __main__ 守卫，含 client_anon 权限测试）
    'test_payment.py',                     # 脚本式（无 __main__ 守卫，模块级 sys.exit）
    'test_player_matching.py',             # 脚本式（无 __main__ 守卫，模块级直接执行）
    'test_v16_final_fixes.py',             # 脚本式（有 __main__ 守卫，python 直跑）
]

TEST_ADMIN = 'admin'  # 各测试模块在临时库 init_db 时自动创建的默认管理员


def pytest_configure(config):
    config.addinivalue_line(
        'markers',
        'no_auto_login: 跳过 autouse 自动登录（用于匿名 / 权限专项测试）',
    )


def _resolve_module_db(mod):
    """返回该测试模块的临时库路径：优先复用模块自身 _tmp/_tmp_db，否则新建。"""
    tmp = getattr(mod, '_tmp', None) or getattr(mod, '_tmp_db', None)
    if tmp is not None and getattr(tmp, 'name', None):
        return tmp.name
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    setattr(mod, '_tmp', tmp)
    return tmp.name


def _setup_module_db(mod):
    """为单个测试模块建立独立临时库并建表 + 默认 admin + 登录模块级 client。

    关键：覆盖 models.py `from config import DB_PATH` 的陈旧绑定（stale binding），
    使 get_db() / init_db() / app 请求全部落在本模块的临时库上，杜绝跨模块污染。
    """
    import config
    import models

    db_path = _resolve_module_db(mod)
    config.DB_PATH = db_path
    models.DB_PATH = db_path
    models.init_db()  # 建表 + 默认 admin（临时库，密码 = 测试专用值，非真实库）

    client = getattr(mod, 'client', None)
    if client is not None:
        # 经正常登录流程建立合法登录态（不绕过鉴权）
        client.post('/api/auth/login', json={
            'username': TEST_ADMIN,
            'password': os.environ['QCOS_ADMIN_PASSWORD'],
        })
    return client


@pytest.fixture(autouse=True, scope='module')
def _qcos_auth_setup(request):
    """module 级 autouse：为需要登录的测试模块建立独立临时库 + admin 登录。

    仅对「有模块级 client 或 seed()」的模块生效（即需要登录/造数的 6 个受影响文件
    与 test_cashier_history）；其余模块（纯函数/文件测试）零副作用。
    匿名 / 权限专项测试通过 no_auto_login marker 或独立 client 保留原始语义。
    """
    if request.node.get_closest_marker('no_auto_login'):
        yield
        return
    mod = request.module
    if getattr(mod, 'client', None) is None and not hasattr(mod, 'seed'):
        yield  # 该模块不需要鉴权脚手架
        return
    _setup_module_db(mod)
    yield


@pytest.fixture(scope='module')
def S(request):
    """兼容显式依赖 S fixture 的文件（tests/test_cashier_history.py）。

    S 的真实语义（按调用方式还原）：模块级 seed() 返回的种子数据字典，
    测试通过 S['fee_a'] / S['sid_b'] 等键引用预期值。并非登录态/客户端。
    仅 test_cashier_history.py 引用 S；其它文件不依赖本 fixture。
    """
    mod = request.module
    _setup_module_db(mod)
    if not hasattr(mod, 'seed'):
        pytest.fail(f'{mod.__name__} 未定义 seed()，S fixture 不适用')
    return mod.seed()
