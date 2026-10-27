#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
inspect_production_state.py — 青鹭 QCOS 生产环境【严格只读】身份识别脚本。

⛔ 本脚本绝对禁止任何写操作：
  - SQLite 一律以 mode=ro（只读）打开；
  - 不执行 UPDATE / INSERT / DELETE；
  - 不建表、不迁移、不 seed、不调用 init_db；
  - 不修改任何文件、不重启服务。
仅用于：识别并核对“当前 QCOS 生产库身份”，输出可供人工与门店页面交叉验证的数据。
任何一步失败：只输出原因并退出，不尝试修复，不做任何写操作。

用法（在门店电脑的项目根目录）：
    cd C:\Users\PC\Desktop\qinglu-qcos
    .\venv\Scripts\python.exe .\inspect_production_state.py
"""
import os
import sys
import json
import sqlite3
from pathlib import Path

# 允许本脚本作为项目根下的模块，导入同目录的 config / billing
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

# config 在导入时要求 QCOS_SECRET_KEY 环境变量；此处仅设置“本进程内环境”，
# 不写入任何文件、不启动服务、不影响生产密钥。
os.environ.setdefault("QCOS_SECRET_KEY", "inspect_readonly_dummy")

REASONS = []  # 身份判定依据收集


def fail(msg):
    """结构性失败：输出原因并退出（不修复、不写）。"""
    print("\n[STEP_FAILED]", msg)
    sys.exit(2)


def reason(ok, text):
    REASONS.append(("OK " if ok else "NO ", text))


def row_to_dict(row):
    """sqlite3.Row 不支持 .get()；统一转为 dict，便于 .get/keys 安全访问，杜绝 AttributeError。"""
    return {k: row[k] for k in row.keys()}


# --------------------------------------------------------------------------
# 进程识别（仅用 subprocess 读取，绝不写）
# --------------------------------------------------------------------------
def _run(cmd):
    try:
        import subprocess
        out = subprocess.run(cmd, capture_output=True, text=True,
                             errors="ignore", timeout=15)
        return out.stdout or ""
    except Exception:
        return ""


def get_process_by_pid(pid):
    # 优先 PowerShell (Get-CimInstance)，回退 wmic
    ps = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_Process -Filter \"ProcessId=%d\" | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress)" % pid,
    ])
    if ps.strip():
        try:
            d = json.loads(ps)
            return {"pid": d.get("ProcessId"), "exe": d.get("ExecutablePath"),
                    "cmd": d.get("CommandLine")}
        except Exception:
            pass
    w = _run(["wmic", "process", "where", "ProcessId=%d" % pid,
              "get", "ProcessId,ExecutablePath,CommandLine", "/format:csv"])
    for line in w.splitlines():
        parts = line.split(",")
        if len(parts) >= 3 and parts[0].strip() == str(pid):
            return {"pid": pid, "exe": parts[1].strip(), "cmd": parts[2].strip()}
    return None


def list_qcos_processes():
    procs = []
    ps = _run([
        "powershell", "-NoProfile", "-Command",
        "(Get-CimInstance Win32_Process -Filter "
        "\"Name='python.exe' OR Name='pythonw.exe' OR Name='python3.exe'\" | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress)",
    ])
    if ps.strip():
        try:
            data = json.loads(ps)
            if isinstance(data, dict):
                data = [data]
            for d in data:
                cmd = (d.get("CommandLine") or "")
                if "qcos_server" in cmd:
                    procs.append({"pid": d.get("ProcessId"),
                                  "exe": d.get("ExecutablePath"), "cmd": cmd})
        except Exception:
            pass
    return procs


# --------------------------------------------------------------------------
# 只读打开 SQLite（mode=ro + query_only 双重防护）
# --------------------------------------------------------------------------
def open_ro(path):
    uri = Path(path).as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        pass
    return conn


def load_billing_settings(conn):
    """只读构建计费 settings：以 config.DEFAULT_SETTINGS 为基线，再用 settings 表覆盖计费键。"""
    try:
        import config
        s = dict(getattr(config, "DEFAULT_SETTINGS", {}))
    except Exception:
        s = {}
    try:
        tcols = [r[1] for r in conn.execute("PRAGMA table_info(settings)").fetchall()]
        if "key" in tcols and "value" in tcols:
            for k, v in conn.execute("SELECT key, value FROM settings").fetchall():
                if (k.startswith("rate_") or k.startswith("overnight_")
                        or k in ("selfservice_multiplier", "rounding")):
                    try:
                        s[k] = float(v) if ("." in str(v) or "e" in str(v).lower()) else int(v)
                    except Exception:
                        pass
    except Exception:
        pass
    return s


def compute_current_fee(machine_type, start_iso, is_overnight, settings):
    """用现有只读计费函数计算实时预估台费；任何异常返回 N/A。"""
    try:
        import billing
        fee, _ = billing.get_current_fee(machine_type, start_iso, settings,
                                         is_overnight=bool(is_overnight))
        return round(fee, 2)
    except Exception as e:
        return "N/A (%s)" % e


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    print("=" * 72)
    print("青鹭 QCOS 生产环境身份识别（严格只读，绝不写库/不重启）")
    print("=" * 72)

    # ---- 1. 项目根目录 & DB_PATH ----
    print("\n[1] 项目根目录与数据库路径")
    print("  PROJECT_ROOT        =", PROJECT_ROOT)
    try:
        import config
        db_path = config.DB_PATH
        print("  DB_PATH(config)     =", db_path)
    except Exception as e:
        db_path = str(PROJECT_ROOT / "qcos.db")
        print("  [警告] 无法 import config（%s），回退 DB_PATH = %s" % (e, db_path))
    print("  DB 文件是否存在      =", os.path.exists(db_path))

    # ---- 2. runtime/qcos.pid & 进程核对 ----
    print("\n[2] 运行进程与 PID 文件")
    pid_file = PROJECT_ROOT / "runtime" / "qcos.pid"
    pid_from_file = None
    pid_file_ok = False
    print("  PID_FILE            =", pid_file)
    if pid_file.exists():
        try:
            pid_from_file = int(pid_file.read_text(encoding="utf-8").strip())
            pid_file_ok = True
            print("  PID(文件)           =", pid_from_file)
        except Exception as e:
            print("  [警告] 读取 PID 文件失败：", e)
    else:
        print("  [警告] 未找到 runtime/qcos.pid（服务可能未以 qcos_server.py 启动，或部署路径不同）")

    qcos_procs = list_qcos_processes()
    print("  当前机器含 'qcos_server' 的 python 进程（参考）：")
    if qcos_procs:
        for p in qcos_procs:
            print("    PID=%-6s EXE=%s" % (p["pid"], p["exe"]))
            print("            CMD=%s" % p["cmd"])
    else:
        print("    （未发现任何 qcos_server 进程）")

    # 严格进程校验：仅当下列【全部】成立，才允许“PID 文件与实际进程一致 = YES”：
    #   ① 确实找到活跃进程；② PID 等于 runtime/qcos.pid；
    #   ③ ExecutablePath 可读取且非空；④ CommandLine 可读取且非空；
    #   ⑤ CommandLine 明确包含当前项目根目录下的 qcos_server.py
    # 任一不满足（含查询权限不足 / 命令失败）→ NO，绝不默认 YES。
    pid_ok = False
    exe_ok = False
    cmd_ok = False
    cmd_points_here = False
    if pid_file_ok and pid_from_file:
        proc = get_process_by_pid(pid_from_file)
        if proc is None:
            print("  [警告] PID=%s 对应进程不存在（进程已退出 / 服务不在运行 / 查询失败）"
                  % pid_from_file)
        else:
            proc_pid = proc.get("pid")
            proc_exe = proc.get("exe")
            proc_cmd = proc.get("cmd")
            print("  PID 文件对应进程信息：")
            print("    PID=%-6s EXE=%s" % (proc_pid, proc_exe))
            print("            CMD=%s" % proc_cmd)
            pid_ok = (proc_pid is not None and int(proc_pid) == pid_from_file)
            exe_ok = bool(proc_exe)            # ExecutablePath 可读取且非空
            cmd_ok = bool(proc_cmd)            # CommandLine 可读取且非空
            if cmd_ok:
                cmd_l = proc_cmd.replace("\\", "/").lower()
                root_l = str(PROJECT_ROOT).replace("\\", "/").lower()
                cmd_points_here = ("qcos_server.py" in cmd_l) and (root_l in cmd_l)
            if pid_ok and not (exe_ok and cmd_ok):
                print("  [警告] 进程 ExecutablePath/CommandLine 无法读取（可能权限不足），"
                      "无法确认身份")
    else:
        print("  [警告] 无有效 PID 文件，无法核对运行进程")

    # 严格一致性判定
    pid_matches = bool(pid_ok and exe_ok and cmd_ok and cmd_points_here)
    print("  → PID 文件与实际进程一致？    %s" % ("YES" if pid_matches else "NO"))
    print("  → 进程 CommandLine 指向本项目的 qcos_server.py？ %s"
          % ("YES" if cmd_points_here else "NO"))

    # ---- 3. 只读打开数据库 ----
    print("\n[3] 以只读方式打开数据库")
    try:
        conn = open_ro(db_path)
        print("  只读连接成功（mode=ro, query_only=ON）")
    except Exception as e:
        fail("无法以只读方式打开数据库：%s\n（请确认 DB_PATH 正确、且当前用户对文件/目录有读取权限；"
             "若处于 WAL 模式且目录不可写，请改用相同用户运行或临时放行目录写权限。）" % e)
        return

    db_ok = True
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for must in ("machines", "sessions"):
            if must not in tables:
                fail("数据库缺少必要表：%s" % must)
                return
    except Exception as e:
        fail("读取表结构失败：%s" % e)
        return

    # ---- 4. machines（只读） ----
    print("\n[4] machines 桌台清单（只读）")
    mcols = [r[1] for r in conn.execute("PRAGMA table_info(machines)").fetchall()]
    msel = "id, name, type"
    if "area" in mcols:
        msel += ", area"
    if "active" in mcols:
        msel += ", active"
    elif "enabled" in mcols:
        msel += ", enabled"
    if "sort_order" in mcols:
        msel += ", sort_order"
    machines = conn.execute("SELECT %s FROM machines ORDER BY id" % msel).fetchall()
    machines = [row_to_dict(r) for r in machines]
    machine_map = {}
    for r in machines:
        machine_map[r["id"]] = r["name"]
        print("   ", dict(r))
    print("  机器总数 =", len(machines))

    # ---- 5. 进行中桌局（只读） ----
    print("\n[5] 进行中桌局（只读）")
    scols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()]
    start_col = "start_time" if "start_time" in scols else ("started_at" if "started_at" in scols else "start_time")
    settings = load_billing_settings(conn)
    active = conn.execute(
        "SELECT * FROM sessions WHERE status IN ('active','paused','running','open')").fetchall()
    if not active:
        active = conn.execute(
            "SELECT * FROM sessions WHERE end_time IS NULL AND (is_voided IS NULL OR is_voided=0)").fetchall()
    active = [row_to_dict(r) for r in active]
    print("  进行中桌局数 =", len(active))
    for s in active:
        sid = s["id"]
        mid = s["machine_id"]
        status = s.get("status")
        start = s.get(start_col)
        players = [p["player_name"] for p in conn.execute(
            "SELECT player_name FROM session_players WHERE session_id=? ORDER BY id", [sid]).fetchall()]
        cons = conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM product_sales WHERE session_id=?", [sid]).fetchone()[0]
        mtype = None
        for m in machines:
            if m["id"] == mid:
                mtype = m["type"]
                break
        live_fee = compute_current_fee(mtype, start, s.get("is_overnight", 1), settings) if mtype else "N/A"
        stored_fee = s.get("fee")
        print("   ---------------------------------------------------------")
        print("    session_id      =", sid)
        print("    machine_id      =", mid, "(%s)" % machine_map.get(mid, "未知"))
        print("    status          =", status)
        print("    start_time      =", start)
        print("    当前玩家        =", players)
        print("    已记录消费      =", cons)
        print("    系统记录台费    =", stored_fee)
        print("    预估台费(实时引擎) =", live_fee)

    # ---- 6. 最近已完成结账（只读） ----
    print("\n[6] 最近结账记录（只读，最多 10 笔）")
    pay_cols = [r[1] for r in conn.execute("PRAGMA table_info(payments)").fetchall()]
    if "created_at" in pay_cols:
        pay_rows = conn.execute(
            "SELECT p.id, p.amount, p.method, p.status, p.created_at, sp.session_id "
            "FROM payments p LEFT JOIN session_players sp ON p.session_player_id = sp.id "
            "ORDER BY p.created_at DESC LIMIT 10").fetchall()
    else:
        pay_rows = conn.execute(
            "SELECT p.id, p.amount, p.method, p.status, NULL AS created_at, sp.session_id "
            "FROM payments p LEFT JOIN session_players sp ON p.session_player_id = sp.id "
            "ORDER BY p.id DESC LIMIT 10").fetchall()
    pay_rows = [row_to_dict(r) for r in pay_rows]
    print("  笔数 =", len(pay_rows))
    for p in pay_rows:
        sid = p["session_id"]
        mid = machine_map and None
        # 通过 session 反查 machine_id
        mname = "N/A"
        if sid:
            row = conn.execute("SELECT machine_id FROM sessions WHERE id=?", [sid]).fetchone()
            if row:
                mname = machine_map.get(row["machine_id"], "未知")
        print("    session_id=%-6s 机器=%s 结账时间=%s 方式=%s 状态=%s 实收=%s"
              % (sid, mname, p["created_at"], p["method"], p["status"], p["amount"]))

    conn.close()

    # ---- 7. 身份判定 ----
    print("\n[7] 生产身份判定")
    db_is_local = (os.path.abspath(db_path) == os.path.abspath(str(PROJECT_ROOT / "qcos.db")))
    reason(cmd_points_here, "运行进程 CommandLine 指向本项目的 qcos_server.py")
    reason(pid_matches, "运行 PID 与 runtime/qcos.pid 一致")
    reason(db_is_local and os.path.exists(db_path), "DB_PATH 是当前项目根目录下的 qcos.db 且存在")
    reason(db_ok and len(machines) > 0, "能正常读取 machines / sessions / 最近结账")
    all_ok = (cmd_points_here and pid_matches and db_is_local and os.path.exists(db_path)
              and db_ok and len(machines) > 0)
    for tag, text in REASONS:
        print("   [%s] %s" % (tag, text))
    print("\n  PRODUCTION_IDENTITY_CONFIRMED = %s" % ("YES" if all_ok else "NO"))
    if all_ok:
        print("  → 技术身份已确认。请在迁移前，将上方 machines / 进行中桌局 / 最近结账")
        print("    与门店【当前 QCOS 页面】逐项人工核对一致；核对无误后方可执行 migrate_tables.py。")
    else:
        print("  → 身份未完全确认，请勿执行任何写操作/迁移。按上方 NO 项排查后再运行本脚本。")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
