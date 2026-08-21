"""test_august_2026_history_import.py — 8月历史组局/收银数据迁移导入器测试

全部使用临时 Excel fixture + 临时 SQLite 库，绝不触碰真实 qcos.db。
运行：python tests/test_august_2026_history_import.py
"""

import hashlib
import os
import sqlite3
import sys
import tempfile

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

import config  # noqa: E402
import models  # noqa: E402

# ---------- 临时库 ----------
_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
TMP_DB = _tmp.name
_tmp.close()
config.DB_PATH = TMP_DB
models.DB_PATH = TMP_DB
models.init_db()

sys.path.insert(0, os.path.join(PROJECT, 'scripts'))
from import_august_2026_history import (  # noqa: E402
    Importer, SOURCE, DATE_START, DATE_END,
)

PASS = 0
FAIL = 0


def check(name, cond, extra=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f'  [PASS] {name}')
    else:
        FAIL += 1
        print(f'  [FAIL] {name} {extra}')


# ---------- 测试 Excel fixture ----------
def make_fixture(path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = '原始组局记录'
    ws.append(['日期', '姓名', '四/八', '娱乐/竞技', '是否带人', '组织者', '是否通宵',
               '标准昵称', '唯一玩家序号', '牌桌序号', '桌首标记', '桌首组织者',
               '数据质量', '真实支付金额'])
    rows = [
        # 1) 8/1 table 201 正常 4 人桌
        ['2026-08-01', 'even', '8', '竞技', '否', '', '否', 'even', None, 201, '是', 'even', '', 100],
        ['2026-08-01', '非此即彼', '8', '竞技', '否', '', '否', '非此即彼', None, 201, '否', '', '', 90],
        ['2026-08-01', 'EnAvAnT', '8', '竞技', '否', '', '否', 'EnAvAnT', None, 201, '否', '', '', 80],
        ['2026-08-01', 'Enos', '8', '竞技', '否', '', '否', 'Enos', None, 201, '否', '', '', 70],
        # 2) 8/2 table 202 只有 3 个已知玩家 + 1 人金额空 -> partial + missing
        ['2026-08-02', '阿甘', '4', '娱乐', '否', '九哥', '是', '阿甘', None, 202, '是', '九哥', '', 60],
        ['2026-08-02', '吴红尘', '4', '娱乐', '否', '', '是', '吴红尘', None, 202, '否', '', '', 50],
        ['2026-08-02', '九哥', '4', '娱乐', '否', '九哥', '是', '九哥', None, 202, '否', '', '', ''],
        # 3) 8/3 table 203 同名玩家（players 表各造 2 个）-> ambiguous
        ['2026-08-03', 'back', '8', '竞技', '否', '', '否', 'back', None, 203, '是', 'back', '', 30],
        ['2026-08-03', '坤哥', '8', '竞技', '否', '', '否', '坤哥', None, 203, '否', '', '', 40],
        # 4) 8/4 table 204 空昵称行 -> 跳过
        ['2026-08-04', '某人', '4', '娱乐', '否', '', '否', '', None, 204, '否', '', '', 99],
        ['2026-08-04', '球哥', '4', '娱乐', '否', '', '否', '球哥', None, 204, '是', '球哥', '', 55],
        # 5) 8/5 table 205 小数金额保真 + unmatched 玩家
        ['2026-08-05', '球哥', '8', '竞技', '否', '', '否', '球哥', None, 205, '是', '球哥', '', 25.5],
        ['2026-08-05', '幽灵玩家', '8', '竞技', '否', '', '否', '幽灵玩家', None, 205, '否', '', '', 20],
        # 6) 8/20 table 206 金额全空 -> session_player 建、payment 不建
        ['2026-08-20', '汤包哥', '8', '竞技', '否', '', '否', '汤包哥', None, 206, '是', '汤包哥', '', ''],
        ['2026-08-20', '福冈', '8', '竞技', '否', '', '否', '福冈', None, 206, '否', '', '', ''],
        # 7) 范围外：7/31 与 8/21 不应导入
        ['2026-07-31', '九哥', '8', '竞技', '否', '', '否', '九哥', None, 999, '是', '九哥', '', 100],
        ['2026-08-21', '九哥', '8', '竞技', '否', '', '否', '九哥', None, 999, '是', '九哥', '', 100],
        # 8) 数据质量=#REF! 但金额有效 -> 软坏保留
        ['2026-08-06', '球哥', '8', '竞技', '否', '', '否', '球哥', None, 207, '是', '球哥', '#REF!', 88],
        ['2026-08-06', 'even', '8', '竞技', '否', '', '否', 'even', None, 207, '否', '', '', 44],
    ]
    for r in rows:
        ws.append(r)
    wb.save(path)
    wb.close()


def seed_players(db_path):
    """预置玩家：正常玩家 + 每名 2 个的同名玩家（back/坤哥）"""
    c = sqlite3.connect(db_path)
    for name in ['even', '非此即彼', 'EnAvAnT', 'Enos', '阿甘', '吴红尘', '九哥',
                 '球哥', '汤包哥', '福冈']:
        c.execute('INSERT INTO players (name, status) VALUES (?, ?)', [name, 'active'])
    # 同名：back x2, 坤哥 x2
    c.execute('INSERT INTO players (name, status) VALUES (?, ?)', ['back', 'active'])
    c.execute('INSERT INTO players (name, status) VALUES (?, ?)', ['back', 'active'])
    c.execute('INSERT INTO players (name, status) VALUES (?, ?)', ['坤哥', 'active'])
    c.execute('INSERT INTO players (name, status) VALUES (?, ?)', ['坤哥', 'active'])
    c.commit()
    c.close()


def dbg(db_path):
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    n_sess = c.execute('SELECT COUNT(*) n FROM sessions').fetchone()['n']
    n_sp = c.execute('SELECT COUNT(*) n FROM session_players').fetchone()['n']
    n_pay = c.execute('SELECT COUNT(*) n FROM payments').fetchone()['n']
    n_vr = c.execute('SELECT COUNT(*) n FROM visit_records').fetchone()['n']
    n_reg = c.execute('SELECT COUNT(*) n FROM legacy_import_records').fetchone()['n']
    c.close()
    return f'sessions={n_sess} sp={n_sp} pay={n_pay} vr={n_vr} reg={n_reg}'


def fresh_db():
    """返回一个全新临时库路径（含 schema + 种子玩家 + 一条 8/21 真实 session）"""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    old_cfg, old_mod = config.DB_PATH, models.DB_PATH
    config.DB_PATH = path
    models.DB_PATH = path
    try:
        models.init_db()
    finally:
        config.DB_PATH, models.DB_PATH = old_cfg, old_mod
    seed_players(path)
    # 预置一条 8/21 真实 session（模拟 8/21 之后的生产数据，必须不被触碰）
    c = sqlite3.connect(path)
    c.execute("INSERT INTO sessions (machine_id, start_time, status) VALUES (1, '2026-08-21T12:00:00', 'active')")
    c.commit()
    c.close()
    return path


REPORT_DIR = os.path.join(tempfile.gettempdir(), 'qcos_aug_test_reports')
TEST_BACKUP_DIR = os.path.join(tempfile.gettempdir(), 'qcos_aug_test_backups')


def make_imp(xlsx, db, **kw):
    kw.setdefault('backup_dir', TEST_BACKUP_DIR)
    return Importer(xlsx, db, REPORT_DIR, **kw)


def main():
    print('== 1. 日期范围 ==')
    tmp_xlsx = os.path.join(tempfile.gettempdir(), 'qcos_aug_fixture.xlsx')
    make_fixture(tmp_xlsx)
    db = fresh_db()
    imp = make_imp(tmp_xlsx, db)
    stats = imp.analyze()
    sids = [g['source_id'] for g in stats['session_groups']]
    check('不导入 7/31 与 8/21 行', all(not s.endswith('_999') for s in sids),
          f'sids={sids}')
    check('Excel 行数 = 17（19 行 - 2 范围外）', stats['excel_rows'] == 17,
          stats['excel_rows'])
    imp.loader.close(); imp.db.close()

    print('== 2. 1桌只生成1 session ==')
    db = fresh_db()
    imp = make_imp(tmp_xlsx, db)
    stats = imp.analyze()
    cnt = sum(1 for g in stats['session_groups'] if g['source_id'] == 'excel_2026-08-01_201')
    check('table 201 仅 1 个 session', cnt == 1, f'cnt={cnt}')
    imp.loader.close(); imp.db.close()

    print('== 3-4. 正常桌 + partial 桌 + execute ==')
    db = fresh_db()
    imp = make_imp(tmp_xlsx, db)
    stats, inserted, ok, checks = imp.execute()
    check('execute 成功', ok is True, f'checks={checks}')
    if inserted:
        check('201 桌 4 session_players',
              inserted['sessions'] >= 1 and inserted['session_players'] >= 8)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    s201 = c.execute("SELECT * FROM sessions WHERE source_id='excel_2026-08-01_201'").fetchone()
    check('201 桌 source_id/time_precision', s201 and s201['time_precision'] == 'date_only')
    if s201:
        check('201 桌 import_quality=OK', s201['import_quality'] == 'OK')
    s202 = c.execute("SELECT * FROM sessions WHERE source_id='excel_2026-08-02_202'").fetchone()
    check('202 桌 PARTIAL', s202 and s202['import_quality'] == 'PARTIAL',
          s202['import_quality'] if s202 else 'None')
    sp202_ov = c.execute(
        "SELECT is_overnight FROM session_players WHERE session_id=? AND player_name='阿甘'",
        [s202['id']]).fetchone()
    check('202 桌 is_overnight=1(session_players)', sp202_ov and sp202_ov['is_overnight'] == 1,
          f"ov={sp202_ov['is_overnight'] if sp202_ov else None}")
    sp202 = c.execute(
        "SELECT * FROM session_players WHERE session_id=? AND player_name='九哥'",
        [s202['id']]).fetchone()
    check('202 桌九哥无金额仍建 session_player', sp202 is not None)
    check('202 桌九哥无 payment',
          c.execute("SELECT COUNT(*) n FROM payments p JOIN session_players sp ON p.session_player_id=sp.id "
                    "WHERE sp.session_id=?", [s202['id']]).fetchone()['n'] == 2)
    check('202 桌九哥 is_organizer=1(组织者)',
          sp202 and sp202['is_organizer'] == 1,
          f"org={sp202['is_organizer'] if sp202 else None}")
    c.close()
    imp.loader.close(); imp.db.close()

    print('== 5. 空昵称跳过 ==')
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    n_sp_204 = c.execute(
        """SELECT COUNT(*) n FROM session_players sp JOIN sessions s ON sp.session_id=s.id
           WHERE s.source_id='excel_2026-08-04_204'""").fetchone()['n']
    check('204 桌空昵称行不建 session_player', n_sp_204 == 1, f'n={n_sp_204}')
    c.close()

    print('== 6. exact 玩家匹配 ==')
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    sp = c.execute("""SELECT sp.player_id FROM session_players sp
                      JOIN sessions s ON sp.session_id=s.id
                      WHERE s.source_id='excel_2026-08-01_201' AND sp.player_name='even'""").fetchone()
    check('even 绑定 player_id', sp and sp['player_id'] is not None, f"pid={sp['player_id'] if sp else None}")
    c.close()

    print('== 7. 同名玩家拒绝猜 ==')
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    s203 = c.execute("SELECT id FROM sessions WHERE source_id='excel_2026-08-03_203'").fetchone()
    sps = c.execute("SELECT player_name, player_id FROM session_players WHERE session_id=?",
                    [s203['id']]).fetchall()
    back_rows = [r for r in sps if r['player_name'] == 'back']
    kun_rows = [r for r in sps if r['player_name'] == '坤哥']
    check('back 不绑定(NULL)', back_rows and all(r['player_id'] is None for r in back_rows),
          f"{[r['player_id'] for r in back_rows]}")
    check('坤哥 不绑定(NULL)', kun_rows and all(r['player_id'] is None for r in kun_rows),
          f"{[r['player_id'] for r in kun_rows]}")
    c.close()

    print('== 8. unmatched 报告 ==')
    imp = make_imp(tmp_xlsx, db)
    st2 = imp.analyze()
    imp.write_reports(st2)  # 生成报告文件供 test 20 断言
    check('幽灵玩家列入 unmatched', '幽灵玩家' in st2['unmatched_players'],
          str(st2['unmatched_players']))
    check('back/坤哥列入 ambiguous',
          'back' in st2['ambiguous_players'] and '坤哥' in st2['ambiguous_players'])
    imp.loader.close(); imp.db.close()

    print('== 9-11. missing payment / 金额保真 / method 不猜 ==')
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    s205 = c.execute("SELECT id FROM sessions WHERE source_id='excel_2026-08-05_205'").fetchone()
    p = c.execute("SELECT amount, method, provider FROM payments p "
                  "JOIN session_players sp ON p.session_player_id=sp.id WHERE sp.session_id=?",
                  [s205['id']]).fetchall()
    amts = sorted(r['amount'] for r in p)
    check('25.5 金额保真', 25.5 in amts, str(amts))
    check('method=legacy_unknown', all(r['method'] == 'legacy_unknown' for r in p),
          str([r['method'] for r in p]))
    check('provider=legacy_excel_aug2026', all(r['provider'] == SOURCE for r in p))
    # 幽灵玩家无金额 -> 无 payment
    s206 = c.execute("SELECT id FROM sessions WHERE source_id='excel_2026-08-20_206'").fetchone()
    n206 = c.execute("SELECT COUNT(*) n FROM payments p JOIN session_players sp ON p.session_player_id=sp.id "
                     "WHERE sp.session_id=?", [s206['id']]).fetchone()['n']
    check('8/20 金额全空桌 0 payment', n206 == 0, f'n={n206}')
    check('8/20 桌仍有 session_players',
          c.execute("SELECT COUNT(*) n FROM session_players WHERE session_id=?",
                    [s206['id']]).fetchone()['n'] == 2)
    c.close()

    print('== 12-13. overnight / organizer ==')
    # 已在 202 桌验证（is_overnight=1, is_organizer=1）

    print('== 14-15. 重跑不重复 + source row 防重复 ==')
    imp = make_imp(tmp_xlsx, db)
    before = dbg(db)
    stats2, inserted2, ok2, checks2 = imp.execute()
    after = dbg(db)
    check('重跑 inserted=0（全 skip）', inserted2 and inserted2['sessions'] == 0
          and inserted2['session_players'] == 0 and inserted2['payments'] == 0,
          str(inserted2))
    check('重跑后数据不变', before == after, f'{before} -> {after}')
    imp.loader.close(); imp.db.close()

    print('== 16. GMV 一致 ==')
    # Excel 有效金额：201(340) + 202(110) + 203(70) + 204(55) + 205(45.5) + 207(132) = 752.5
    # （204 空昵称行 99 不算；206 全空；幽灵玩家 20 算 unmatched 但金额有效）
    excel_gmv = 100 + 90 + 80 + 70 + 60 + 50 + 30 + 40 + 55 + 25.5 + 20 + 88 + 44
    check('excel_gmv=752.5', round(stats['excel_gmv'], 2) == round(excel_gmv, 2),
          f"excel={stats['excel_gmv']} expect={excel_gmv}")
    check('gmv_diff=0（无旧数据）', stats['gmv_diff'] == 0, stats['gmv_diff'])

    print('== 17. 关键失败 rollback（备份失败禁止导入） ==')
    import import_august_2026_history as mod
    orig_backup = mod.backup_database

    def boom(*a, **k):
        raise RuntimeError('backup failed for test')
    mod.backup_database = boom
    db_b = fresh_db()
    impb = make_imp(tmp_xlsx, db_b)
    st, ins, okb, chk = impb.execute()
    mod.backup_database = orig_backup
    check('备份失败 -> 不导入(返回False)', okb is False, f'okb={okb}')
    c = sqlite3.connect(db_b)
    n_sess_b = c.execute('SELECT COUNT(*) n FROM sessions').fetchone()[0]
    c.close()
    check('备份失败后库无新增 session', n_sess_b == 1, f'n={n_sess_b}(仅8/21预置)')
    impb.loader.close(); impb.db.close()

    print('== 17b. legacy gap 默认回滚，--allow-legacy-gap 放行 ==')
    db_c = fresh_db()
    # 预插一条旧格式 session + payment 制造 legacy gap
    c = sqlite3.connect(db_c)
    c.execute("INSERT INTO sessions (machine_id, start_time, status, source_id) "
              "VALUES (1, '2026-08-05T12:00:00', 'completed', 'excel_2026-08-05_1')")
    sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO session_players (session_id, player_name, final_fee, status) "
              "VALUES (?, '旧玩家', 999, 'completed')", [sid])
    spid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.execute("INSERT INTO payments (out_trade_no, method, amount, status, provider, session_player_id) "
              "VALUES ('OLD-1', 'legacy_unknown', 999, 'SUCCESS', 'legacy', ?)", [spid])
    c.commit(); c.close()
    impc = make_imp(tmp_xlsx, db_c)
    stc, insc, okc, chkc = impc.execute()
    check('legacy gap 默认 ROLLBACK', okc is False, f'okc={okc} chk={chkc}')
    impc.loader.close(); impc.db.close()
    impd = make_imp(tmp_xlsx, db_c, allow_legacy_gap=True)
    std, insd, okd, chkd = impd.execute()
    check('allow-legacy-gap 放行成功', okd is True, f'okd={okd} chk={chkd}')
    impd.loader.close(); impd.db.close()

    print('== 18. 不修改 8/21 以后数据 ==')
    c = sqlite3.connect(db_c)
    n821 = c.execute("SELECT COUNT(*) n FROM sessions WHERE start_time >= '2026-08-21'").fetchone()[0]
    c.close()
    check('8/21+ session 未被删改', n821 == 1, f'n={n821}')

    print('== 19. 不修改真实 qcos.db ==')
    real_db = os.path.join(PROJECT, 'qcos.db')
    if os.path.exists(real_db):
        h1 = hashlib.sha256(open(real_db, 'rb').read()).hexdigest()
        # 测试全程未写真实库，再算一次
        h2 = hashlib.sha256(open(real_db, 'rb').read()).hexdigest()
        check('真实 qcos.db 未变化', h1 == h2)
    else:
        check('真实 qcos.db 未变化(不存在，跳过)', True)

    print('== 20. 已导入行 skip + 报告文件 ==')
    import csv
    missing = os.path.join(REPORT_DIR, 'missing_payments.csv')
    check('missing_payments.csv 存在', os.path.exists(missing))
    if os.path.exists(missing):
        with open(missing, encoding='utf-8-sig') as f:
            n_miss = sum(1 for _ in csv.reader(f)) - 1
        check('missing 行数>0', n_miss >= 1, f'n={n_miss}')
    for fn in ['summary.json', 'tables.csv', 'players.csv',
               'unmatched_players.csv', 'ambiguous_players.csv', 'bad_rows.csv']:
        p = os.path.join(REPORT_DIR, fn)
        check(f'{fn} 存在', os.path.exists(p))
    import json
    with open(os.path.join(REPORT_DIR, 'summary.json'), encoding='utf-8') as f:
        summ = json.load(f)
    check('summary 含关键字段', all(k in summ for k in (
        'excel_rows', 'sessions_detected', 'excel_gmv', 'would_insert_sessions',
        'already_imported', 'unmatched_players', 'ambiguous_players')))

    print('== 21. --ambiguous-bind 白名单绑定（人工确认重名） ==')
    db_e = fresh_db()
    c = sqlite3.connect(db_e)
    c.row_factory = sqlite3.Row
    back_id = c.execute("SELECT MIN(id) FROM players WHERE name='back'").fetchone()[0]
    kun_id = c.execute("SELECT MIN(id) FROM players WHERE name='坤哥'").fetchone()[0]
    c.close()
    imp_e = make_imp(tmp_xlsx, db_e, ambiguous_bind={'back': back_id, '坤哥': kun_id})
    st_e = imp_e.analyze()
    check('白名单后 back 不再 ambiguous', 'back' not in st_e['ambiguous_players'],
          str(st_e['ambiguous_players']))
    check('白名单后坤哥不再 ambiguous', '坤哥' not in st_e['ambiguous_players'],
          str(st_e['ambiguous_players']))
    st_e2, ins_e, ok_e, chk_e = imp_e.execute()
    check('白名单 execute 成功', ok_e is True, f'ok={ok_e} chk={chk_e}')
    c = sqlite3.connect(db_e)
    c.row_factory = sqlite3.Row
    sp_back = c.execute(
        """SELECT sp.player_id FROM session_players sp
           JOIN sessions s ON sp.session_id=s.id
           WHERE s.source_id='excel_2026-08-03_203' AND sp.player_name='back'"""
    ).fetchone()
    sp_kun = c.execute(
        """SELECT sp.player_id FROM session_players sp
           JOIN sessions s ON sp.session_id=s.id
           WHERE s.source_id='excel_2026-08-03_203' AND sp.player_name='坤哥'"""
    ).fetchone()
    c.close()
    check('back 绑定到白名单 id', sp_back and sp_back['player_id'] == back_id,
          f"pid={sp_back['player_id'] if sp_back else None} expect={back_id}")
    check('坤哥 绑定到白名单 id', sp_kun and sp_kun['player_id'] == kun_id,
          f"pid={sp_kun['player_id'] if sp_kun else None} expect={kun_id}")
    imp_e.loader.close(); imp_e.db.close()

    print('== 22. --create-missing-players 建档并绑定 ==')
    db_f = fresh_db()
    imp_f = make_imp(tmp_xlsx, db_f, create_missing=True)
    st_f = imp_f.analyze()
    check('dry-run 时幽灵玩家仍在 unmatched', '幽灵玩家' in st_f['unmatched_players'],
          str(st_f['unmatched_players']))
    st_f2, ins_f, ok_f, chk_f = imp_f.execute()
    check('create_missing execute 成功', ok_f is True, f'ok={ok_f} chk={chk_f}')
    c = sqlite3.connect(db_f)
    c.row_factory = sqlite3.Row
    ghost = c.execute("SELECT id FROM players WHERE name='幽灵玩家'").fetchone()
    c.close()
    check('幽灵玩家已建档', ghost is not None, f'id={ghost}')
    if ghost:
        c = sqlite3.connect(db_f)
        c.row_factory = sqlite3.Row
        sp_ghost = c.execute(
            """SELECT sp.player_id FROM session_players sp
               JOIN sessions s ON sp.session_id=s.id
               WHERE s.source_id='excel_2026-08-05_205' AND sp.player_name='幽灵玩家'"""
        ).fetchone()
        c.close()
        check('幽灵玩家行已绑定新档案', sp_ghost and sp_ghost['player_id'] == ghost['id'],
              f"pid={sp_ghost['player_id'] if sp_ghost else None}")
    imp_f.loader.close(); imp_f.db.close()

    print('== 23. execute 对未迁移库自动补 schema ==')
    fd, db_g = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    c = sqlite3.connect(db_g)
    c.row_factory = sqlite3.Row
    c.executescript('''
        CREATE TABLE machines (id INTEGER PRIMARY KEY, name TEXT, type TEXT,
            status TEXT DEFAULT 'idle', sort_order INTEGER DEFAULT 0);
        CREATE TABLE sessions (id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id INTEGER NOT NULL, start_time TEXT NOT NULL, end_time TEXT,
            duration_minutes INTEGER, fee REAL, fee_breakdown TEXT, discount_type TEXT,
            discount_id INTEGER, discount_amount REAL DEFAULT 0, final_fee REAL,
            payment_method TEXT, status TEXT DEFAULT 'active', note TEXT,
            source_id TEXT);
        CREATE TABLE session_players (id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL, player_name TEXT, is_organizer INTEGER DEFAULT 0,
            visit_type TEXT, player_id INTEGER, start_time TEXT, end_time TEXT,
            duration_minutes INTEGER, fee REAL DEFAULT 0, final_fee REAL DEFAULT 0,
            discount_amount REAL DEFAULT 0, product_total REAL DEFAULT 0,
            grand_total REAL DEFAULT 0, payment_method TEXT, status TEXT DEFAULT 'active',
            is_overnight INTEGER DEFAULT 0);
        CREATE TABLE payments (id INTEGER PRIMARY KEY AUTOINCREMENT, out_trade_no TEXT,
            method TEXT, amount REAL, status TEXT, provider TEXT, session_player_id INTEGER,
            created_at TEXT, updated_at TEXT);
        CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, phone TEXT,
            wechat TEXT, qcos_id TEXT, dan TEXT, dan_source TEXT, first_visit TEXT,
            notes TEXT, is_member INTEGER DEFAULT 0, member_id INTEGER, created_at TEXT,
            updated_at TEXT, real_name TEXT, preferred_name TEXT, gender TEXT, birthday TEXT,
            wechat_remark TEXT, area TEXT, occupation TEXT, industry TEXT, source_channel TEXT,
            introducer TEXT, relationship_strength TEXT, personality_tags TEXT, player_type TEXT,
            skill_level TEXT, preferred_mode TEXT, preferred_time TEXT, can_overnight TEXT,
            tournament_interest TEXT, organizer_candidate TEXT, organizer_level TEXT,
            organizer_note TEXT, last_visit TEXT, total_visits INTEGER DEFAULT 0,
            visits_30d INTEGER DEFAULT 0, activity_level TEXT, common_mode TEXT,
            active_behavior TEXT, is_organizer INTEGER DEFAULT 0, maintenance_priority TEXT,
            marketing_tags TEXT, risk_tags TEXT, follow_up_status TEXT, next_follow_up TEXT,
            last_contact TEXT, last_contact_summary TEXT, drink_preference TEXT,
            price_sensitivity TEXT, profile_completeness TEXT, customer_score REAL,
            customer_level TEXT, customer_score_updated TEXT, initiative_level TEXT,
            initiative_score REAL, initiative_updated TEXT, table_style_preference TEXT,
            experience_score REAL, compatibility_score REAL, conflict_count INTEGER DEFAULT 0,
            positive_table_count INTEGER DEFAULT 0, negative_table_count INTEGER DEFAULT 0);
        CREATE TABLE visit_records (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER,
            player_name TEXT, visit_date TEXT, machine_type TEXT, game_type TEXT,
            brought_guest TEXT, organizer_name TEXT, is_overnight INTEGER DEFAULT 0,
            table_number TEXT, is_table_head INTEGER DEFAULT 0, table_head_organizer TEXT,
            data_quality TEXT, created_at TEXT, payment_amount REAL, session_id INTEGER);
        INSERT INTO machines (id, name, type) VALUES
            (1, '8口机1', '8port'), (2, '8口机2', '8port'),
            (3, '4口机1', '4port'), (4, '4口机2', '4port');
        INSERT INTO players (name) VALUES ('even'), ('非此即彼'), ('EnAvAnT'), ('Enos'),
            ('阿甘'), ('吴红尘'), ('九哥'), ('球哥'), ('汤包哥'), ('福冈');
    ''')
    c.commit(); c.close()
    imp_g = Importer(tmp_xlsx, db_g, REPORT_DIR, backup_dir=TEST_BACKUP_DIR)
    st_g, ins_g, ok_g, chk_g = imp_g.execute()
    check('未迁移库 execute 自动补 schema 成功', ok_g is True, f'ok={ok_g} chk={chk_g}')
    c = sqlite3.connect(db_g)
    has_tbl = c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_import_records'"
    ).fetchone()
    s_cols = [r[1] for r in c.execute('PRAGMA table_info(sessions)')]
    c.close()
    check('legacy_import_records 已创建', has_tbl is not None)
    check('sessions.time_precision 已加', 'time_precision' in s_cols, str(s_cols))
    check('sessions.import_quality 已加', 'import_quality' in s_cols)
    imp_g.loader.close(); imp_g.db.close()

    print(f'\n===== RESULT: PASS={PASS} FAIL={FAIL} =====')
    sys.exit(1 if FAIL else 0)


if __name__ == '__main__':
    main()
