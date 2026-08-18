"""QCOS V1.1 运营大脑 - 单元测试
使用临时数据库，不污染生产 qcos.db。
覆盖：数据库迁移、玩家评分模型、任务生成去重、GMV模块、驾驶舱/工作台结构、
任务完成流转，以及通过 Flask test_client 的端到端 API 验证。
"""
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===== 测试专用环境变量（必须在 import models / config 之前设置，非生产凭据）=====
os.environ.setdefault('QCOS_SECRET_KEY', 'test_secret_key_qcos_unit_test')
os.environ.setdefault('QCOS_ADMIN_PASSWORD', 'test_admin_pw_qcos_v2')

import models
import operations

# 切换到临时数据库（修改 models 模块内的 DB_PATH 全局变量）
TMP_DB = tempfile.mktemp(suffix='.db')
models.DB_PATH = TMP_DB
models.init_db()

db = models.get_db()


def add_session(db, pid, pname, days_ago, grand_total=100, is_organizer=0):
    dt = (date.today() - timedelta(days=days_ago)).isoformat() + 'T14:00:00'
    db.execute("INSERT INTO sessions (machine_id,start_time,end_time,status) VALUES (1,?,?,'closed')", [dt, dt])
    sid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        """INSERT INTO session_players (session_id,player_name,player_id,is_organizer,grand_total,final_fee,status)
           VALUES (?,?,?,?,?,?,'checked_out')""",
        [sid, pname, pid, is_organizer, grand_total, grand_total])


# ---------- 构造测试数据 ----------
db.execute("INSERT INTO players (name,is_organizer,birthday) VALUES ('张三',1,'1990-08-10')")
p1 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
db.execute("INSERT INTO players (name) VALUES ('李四')")
p2 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
db.execute("INSERT INTO players (name) VALUES ('王五')")
p3 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
db.execute("INSERT INTO players (name,risk_tags) VALUES ('赵六','欠账投诉')")
p4 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
# 孙七：A级，11天前最后到店（触发A级召回），带客3次、组织者
db.execute("INSERT INTO players (name,is_organizer) VALUES ('孙七',1)")
p5 = db.execute("SELECT last_insert_rowid()").fetchone()[0]
db.commit()

# 张三：A+级，近3天高频高消费，带客3次（每次到店一条 visit_records）
add_session(db, p1, '张三', 0, 1500, 1)
add_session(db, p1, '张三', 1, 1500, 1)
add_session(db, p1, '张三', 2, 1500, 1)
for d in (3, 4, 5):
    db.execute(
        "INSERT INTO visit_records (player_id,player_name,visit_date,brought_guest,is_table_head) VALUES (?,?,?,?,?)",
        [p1, '张三', (date.today() - timedelta(days=d)).isoformat(), 1, 0])
# 孙七：A级，8-11天前高频高消费，带客3次、组织者（带客记录同期，使其末次到店=11天前）
for d in (8, 9, 10, 11):
    add_session(db, p5, '孙七', d, 2000, 1)
for d in (9, 10, 11):
    db.execute(
        "INSERT INTO visit_records (player_id,player_name,visit_date,brought_guest,is_table_head) VALUES (?,?,?,?,?)",
        [p5, '孙七', (date.today() - timedelta(days=d)).isoformat(), 1, 0])
# 李四：B级，15-25天前到店，触发B级召回
for d in (15, 18, 20, 25):
    add_session(db, p2, '李四', d, 800, 0)
# 王五：新客，1-2天前各1次
add_session(db, p3, '王五', 1, 50, 0)
add_session(db, p3, '王五', 2, 50, 0)
# 赵六：风险玩家，10天前1次
add_session(db, p4, '赵六', 10, 50, 0)
db.commit()
db.close()


# ---------- 断言框架 ----------
fails = []
def check(name, cond, extra=''):
    status = 'PASS' if cond else 'FAIL'
    print(f"[{status}] {name}" + (f"  | {extra}" if extra else ''))
    if not cond:
        fails.append(name)


# ---------- 1. 数据库迁移 ----------
db = models.get_db()
t = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='operation_tasks'").fetchone()
check('operation_tasks 表已创建', t is not None)
cols = [c['name'] for c in db.execute('PRAGMA table_info(players)').fetchall()]
check('players 含 customer_score', 'customer_score' in cols)
check('players 含 customer_level', 'customer_level' in cols)
check('players 含 customer_score_updated', 'customer_score_updated' in cols)
tg = db.execute("SELECT value FROM settings WHERE key='monthly_target_gmv'").fetchone()
check('monthly_target_gmv 默认 30000', tg is not None and float(tg['value']) == 30000, str(tg))

# ---------- 2. 玩家价值评分 ----------
res = operations.compute_player_scores(db)
check('compute_player_scores 覆盖全部玩家', len(res) == 5, f"len={len(res)}")
levels = {r['player_id']: r['customer_level'] for r in res}
for r in res:
    check(f"评分等级合法[{r['name']}]", r['customer_level'] in ('A+', 'A', 'B', 'C', 'D'),
          f"{r['customer_level']}/{r['customer_score']}")
check('张三为 A 或 A+', levels[p1] in ('A+', 'A'), levels[p1])
check('赵六因风险降为 D', levels[p4] == 'D', levels[p4])
row = db.execute("SELECT customer_score,customer_level FROM players WHERE id=?", [p1]).fetchone()
check('评分写回 players 表', row['customer_level'] in ('A+', 'A'))

# ---------- 3. 任务生成与去重 ----------
s1 = operations.generate_operation_tasks(db)
check('首次生成有任务', s1['total'] > 0, str(s1))
check('含组织者培养', s1['created']['organizer_develop'] >= 1, str(s1['created']))
check('含新客转化', s1['created']['new_customer_follow'] >= 1)
check('含客户召回', s1['created']['recover_customer'] >= 1)
check('含风险预警', s1['created']['risk_warning'] >= 1)
s2 = operations.generate_operation_tasks(db)
check('二次生成去重（跳过已存在）', s2['skipped_existing'] > 0 and s2['total'] == 0, str(s2))

# ---------- 4. GMV 模块 ----------
g = operations.get_gmv_summary(db)
check('GMV 目标 = 30000', g['target'] == 30000, str(g['target']))
check('GMV 本月累计 > 0', g['month_gmv'] > 0, str(g['month_gmv']))
check('GMV 完成率为数值', isinstance(g['completion_pct'], (int, float)))

# ---------- 5. 驾驶舱 / 工作台结构 ----------
dash = operations.get_operations_dashboard(db)
check('dashboard 含 overview', 'overview' in dash)
check('dashboard 含 gmv', 'gmv' in dash)
check('dashboard 含 forecast', 'forecast' in dash)
check('dashboard.tasks 含 5 类', all(k in dash['tasks'] for k in
     ('recover_customer', 'maintain_customer', 'new_customer_follow', 'organizer_develop', 'risk_warning')))
staff = operations.get_staff_dashboard(db)
check('staff 含 tasks 列表', isinstance(staff['tasks'], list))
check('staff 重点客户含张三', any(c['name'] == '张三' for c in staff['key_customers']),
      str([c['name'] for c in staff['key_customers']]))
check('staff 生日提醒含张三(8月)', any(b['name'] == '张三' for b in staff['birthdays']))

# ---------- 6. 任务完成流转 ----------
pending = db.execute("SELECT id FROM operation_tasks WHERE status='pending' LIMIT 1").fetchone()
tid = pending['id']
ok = operations.complete_task(db, tid, 'tester')
row = db.execute("SELECT status,operator FROM operation_tasks WHERE id=?", [tid]).fetchone()
check('complete_task 成功并记录操作人', ok and row['status'] == 'completed' and row['operator'] == 'tester', str(row))
db.close()


# ---------- 7. 端到端 API（Flask test_client） ----------
try:
    import app as appmod
    client = appmod.app.test_client()
    r = client.post('/api/auth/login', json={'username': 'admin', 'password': os.environ['QCOS_ADMIN_PASSWORD']})
    check('登录接口 200', r.status_code == 200, str(r.status_code))
    r2 = client.get('/api/operations/dashboard')
    check('运营驾驶舱 API 200', r2.status_code == 200, str(r2.status_code))
    d2 = r2.get_json()
    check('驾驶舱 API 返回 tasks', 'tasks' in d2)
    r3 = client.get('/api/operations/staff')
    check('店员工作台 API 200', r3.status_code == 200, str(r3.status_code))
    r4 = client.post('/api/operations/generate-tasks')
    check('生成任务 API 200', r4.status_code == 200, str(r4.status_code))
    check('页面 /operations 可渲染', client.get('/operations').status_code == 200)
    check('页面 /staff_tasks 可渲染', client.get('/staff_tasks').status_code == 200)
except Exception as e:
    check('端到端 API 执行', False, f"异常: {e}")


# ---------- 结果 ----------
print('\n==== 测试结果 ====')
if fails:
    print(f"失败 {len(fails)} 项：{fails}")
else:
    print("全部通过 ✅")
sys.exit(1 if fails else 0)
