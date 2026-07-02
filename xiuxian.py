import asyncio
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_DIR = PROJECT_ROOT / "model"
WATCH_FILES = (PROJECT_ROOT / "xiuxian.py",)
WORKER_ARG = "--worker"
LISTENER_ARG = "--listener"
SCAN_INTERVAL_SEC = 2
RELOAD_STABLE_SEC = 10
WORKER_STOP_TIMEOUT_SEC = 20
LEGACY_PROJECT_ROOTS = (Path("/opt/xiuxian"),)
LEGACY_PROCESS_SCAN_INTERVAL_SEC = 2


def _env_float(name, default, *, minimum=None, maximum=None):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return value


PENDING_DRAIN_POLL_SEC = _env_float("XIUXIAN_PENDING_DRAIN_POLL_SEC", 2, minimum=0.25, maximum=10)
PENDING_DRAIN_GRACE_SEC = _env_float("XIUXIAN_PENDING_DRAIN_GRACE_SEC", 3, minimum=0, maximum=30)
RELOAD_PENDING_DRAIN_MAX_SEC = _env_float("XIUXIAN_RELOAD_PENDING_DRAIN_MAX_SEC", 600, minimum=0, maximum=1800)
STOP_PENDING_DRAIN_MAX_SEC = _env_float("XIUXIAN_STOP_PENDING_DRAIN_MAX_SEC", 75, minimum=0, maximum=300)
RUNTIME_PENDING_FIELDS = (
    ("wild_training_reply_to_msg_id", "wild_training_reply_due_at", "野外历练"),
    ("explore_rift_reply_to_msg_id", "explore_rift_reply_due_at", "探寻裂缝"),
    ("fishing_reply_to_msg_id", "fishing_reply_due_at", "钓鱼"),
    ("mulan_reply_to_msg_id", "mulan_reply_due_at", "慕兰"),
    ("wendao_reply_to_msg_id", "wendao_reply_due_at", "问道"),
    ("duel_reply_to_msg_id", "duel_reply_due_at", "斗法"),
    ("ranch_reply_to_msg_id", "ranch_reply_due_at", "灵兽牧场"),
    ("nanlong_reply_to_msg_id", "nanlong_reply_due_at", "南陇侯"),
    ("last_tower_msg_id", "tower_reply_due_at", "闯塔"),
    ("small_world_preach_reply_to_msg_id", "small_world_preach_due_at", "小世界布道"),
    ("small_world_barrier_msg_id", "small_world_barrier_due_at", "护界禁制"),
    ("second_soul_purge_msg_id", "second_soul_purge_due_at", "元神镇魔"),
)


def _watched_files():
    files = []
    if MODEL_DIR.exists():
        files.extend(path for path in MODEL_DIR.rglob("*.py") if path.is_file())
    files.extend(path for path in WATCH_FILES if path.exists() and path.is_file())
    return sorted(set(files))


def _code_fingerprint():
    fingerprint = []
    for path in _watched_files():
        try:
            stat = path.stat()
            rel_path = path.relative_to(PROJECT_ROOT).as_posix()
        except FileNotFoundError:
            continue
        fingerprint.append((rel_path, stat.st_mtime_ns, stat.st_size))
    return tuple(fingerprint)


def _code_syntax_ok():
    ok = True
    for path in _watched_files():
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
        except Exception:
            ok = False
            traceback.print_exc()
    return ok


def _state_db_path():
    if os.environ.get("XIUXIAN_DB_FILE"):
        return Path(os.environ["XIUXIAN_DB_FILE"])
    if os.environ.get("XIUXIAN_STATE_DIR"):
        state_dir = Path(os.environ["XIUXIAN_STATE_DIR"])
    elif os.environ.get("XIUXIAN_DATA_DIR"):
        state_dir = Path(os.environ["XIUXIAN_DATA_DIR"]) / "state"
    else:
        state_dir = PROJECT_ROOT / "data" / "state"
    return state_dir / "chaogu_state.db"


def _sqlite_table_exists(conn, table):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (str(table),),
    ).fetchone()
    return row is not None


def _safe_float(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _safe_int(value, default=0):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _json_dict(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        loaded = json.loads(value)
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _append_json_pending_window(windows, row, field, msg_key, due_key, action_key, module_name, now):
    payload = _json_dict(row.get(field))
    msg_id = _safe_int(payload.get(msg_key))
    due_at = _safe_float(payload.get(due_key))
    if msg_id <= 0 or due_at + PENDING_DRAIN_GRACE_SEC <= now:
        return
    action = str(payload.get(action_key) or "").strip()
    windows.append({
        "module": module_name if not action else f"{module_name}:{action}",
        "identity_id": _safe_int(row.get("send_as_id")),
        "msg_id": msg_id,
        "due_at": due_at,
    })


def _active_pending_windows(now=None):
    now = float(now if now is not None else time.time())
    db_path = _state_db_path()
    if not db_path.exists():
        return []
    windows = []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=1) as conn:
            conn.row_factory = sqlite3.Row
            if _sqlite_table_exists(conn, "pending_tasks"):
                for row in conn.execute("SELECT * FROM pending_tasks").fetchall():
                    sent_at = _safe_float(row["sent_at"])
                    timeout = _safe_float(row["timeout"])
                    due_at = sent_at + timeout
                    if sent_at > 0 and timeout > 0 and due_at + PENDING_DRAIN_GRACE_SEC > now:
                        windows.append({
                            "module": str(row["source_module"] or row["cmd"] or "pending_tasks"),
                            "identity_id": _safe_int(row["send_as_id"]),
                            "msg_id": _safe_int(row["msg_id"]),
                            "due_at": due_at,
                        })
            if _sqlite_table_exists(conn, "identity_runtime_state"):
                for raw_row in conn.execute("SELECT * FROM identity_runtime_state").fetchall():
                    row = dict(raw_row)
                    identity_id = _safe_int(row.get("send_as_id"))
                    for msg_field, due_field, module_name in RUNTIME_PENDING_FIELDS:
                        msg_id = _safe_int(row.get(msg_field))
                        due_at = _safe_float(row.get(due_field))
                        if msg_id > 0 and due_at + PENDING_DRAIN_GRACE_SEC > now:
                            windows.append({
                                "module": module_name,
                                "identity_id": identity_id,
                                "msg_id": msg_id,
                                "due_at": due_at,
                            })
                    _append_json_pending_window(
                        windows,
                        row,
                        "tianxing_observation",
                        "auto_pending_msg_id",
                        "auto_pending_due_at",
                        "auto_pending_action",
                        "天星",
                        now,
                    )
                    _append_json_pending_window(
                        windows,
                        row,
                        "hehuan_observation",
                        "auto_pending_msg_id",
                        "auto_pending_deadline_at",
                        "auto_pending_action",
                        "合欢",
                        now,
                    )
    except sqlite3.Error as exc:
        print(f"读取 pending 状态失败，跳过 drain：{exc}", flush=True)
        return []
    windows.sort(key=lambda item: (float(item.get("due_at") or 0), int(item.get("identity_id") or 0)))
    return windows


def _format_pending_windows(windows, now, limit=4):
    parts = []
    for item in windows[:limit]:
        due_at = float(item.get("due_at") or 0)
        remaining = max(0, int(due_at - float(now or 0)))
        parts.append(f"{item.get('identity_id')}:{item.get('module')}#{item.get('msg_id')}({remaining}s)")
    if len(windows) > limit:
        parts.append(f"+{len(windows) - limit}")
    return " ".join(parts)


def _wait_for_pending_drain(reason, max_wait_sec):
    max_wait_sec = float(max_wait_sec or 0)
    if max_wait_sec <= 0:
        return False
    started_at = time.time()
    last_log_at = 0.0
    waited = False
    while True:
        now = time.time()
        windows = _active_pending_windows(now)
        if not windows:
            return waited
        remaining_budget = started_at + max_wait_sec - now
        if remaining_budget <= 0:
            print(
                f"{reason} pending drain 已到上限，继续执行；仍有：{_format_pending_windows(windows, now)}",
                flush=True,
            )
            return waited
        if not waited or now - last_log_at >= 10:
            print(
                f"{reason} 延迟：检测到回复窗口未闭合，等待监听入库：{_format_pending_windows(windows, now)}",
                flush=True,
            )
            last_log_at = now
        waited = True
        next_due_at = min(float(item.get("due_at") or now) for item in windows)
        sleep_sec = min(PENDING_DRAIN_POLL_SEC, max(0.25, next_due_at + PENDING_DRAIN_GRACE_SEC - now), remaining_budget)
        time.sleep(sleep_sec)


def _spawn_worker():
    print("启动 worker...", flush=True)
    return subprocess.Popen(
        [sys.executable, str(PROJECT_ROOT / "xiuxian.py"), WORKER_ARG],
        cwd=str(PROJECT_ROOT),
    )


def _read_proc_cmdline(pid):
    try:
        raw = Path("/proc") / str(pid) / "cmdline"
        return raw.read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def _legacy_xiuxian_pids():
    current_pid = os.getpid()
    current_worker = str(PROJECT_ROOT / "xiuxian.py")
    legacy_scripts = {str(root / "xiuxian.py") for root in LEGACY_PROJECT_ROOTS}
    pids = []
    proc_root = Path("/proc")
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == current_pid:
            continue
        cmdline = _read_proc_cmdline(pid)
        if "xiuxian.py" not in cmdline:
            continue
        if current_worker in cmdline:
            continue
        if any(script in cmdline for script in legacy_scripts):
            pids.append((pid, cmdline))
    return pids


def _stop_legacy_xiuxian_processes():
    for pid, cmdline in _legacy_xiuxian_pids():
        try:
            os.kill(pid, signal.SIGKILL)
            print(f"已终止旧 xiuxian 实例 pid={pid}: {cmdline}", flush=True)
        except ProcessLookupError:
            continue
        except Exception as exc:
            print(f"终止旧 xiuxian 实例失败 pid={pid}: {exc}", flush=True)


def _stop_worker(worker):
    if worker is None or worker.poll() is not None:
        return
    print("停止 worker...", flush=True)
    worker.terminate()
    try:
        worker.wait(timeout=WORKER_STOP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        print("worker 优雅退出超时，强制结束。", flush=True)
        worker.kill()
        worker.wait()


def _run_supervisor():
    stop_requested = False

    def request_stop(_signum, _frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    _stop_legacy_xiuxian_processes()
    worker = _spawn_worker()
    current_fingerprint = _code_fingerprint()
    pending_fingerprint = None
    pending_since = 0
    last_legacy_scan = 0.0

    try:
        while not stop_requested:
            now = time.time()
            if now - last_legacy_scan >= LEGACY_PROCESS_SCAN_INTERVAL_SEC:
                last_legacy_scan = now
                _stop_legacy_xiuxian_processes()

            if worker.poll() is not None:
                print(f"worker 已退出，退出码：{worker.returncode}", flush=True)
                if _code_syntax_ok():
                    worker = _spawn_worker()
                    current_fingerprint = _code_fingerprint()
                    pending_fingerprint = None
                    pending_since = 0
                else:
                    time.sleep(SCAN_INTERVAL_SEC)
                    continue

            fingerprint = _code_fingerprint()
            if fingerprint != current_fingerprint:
                if fingerprint != pending_fingerprint:
                    pending_fingerprint = fingerprint
                    pending_since = now
                    print("检测到代码变化，等待覆盖完成...", flush=True)
                elif now - pending_since >= RELOAD_STABLE_SEC:
                    stable_fingerprint = _code_fingerprint()
                    if stable_fingerprint != pending_fingerprint:
                        pending_fingerprint = stable_fingerprint
                        pending_since = now
                        continue
                    if _code_syntax_ok():
                        print("代码已稳定且语法检查通过，准备重启 worker。", flush=True)
                        _wait_for_pending_drain("代码热重载前", RELOAD_PENDING_DRAIN_MAX_SEC)
                        print("重启 worker。", flush=True)
                        _stop_worker(worker)
                        worker = _spawn_worker()
                        current_fingerprint = stable_fingerprint
                        pending_fingerprint = None
                        pending_since = 0
                    else:
                        print("代码语法检查失败，保留当前 worker。", flush=True)
                        pending_since = now
            else:
                pending_fingerprint = None
                pending_since = 0

            time.sleep(SCAN_INTERVAL_SEC)
    finally:
        _wait_for_pending_drain("停止 worker 前", STOP_PENDING_DRAIN_MAX_SEC)
        _stop_worker(worker)


def _run_worker():
    from model.app import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()


def _run_listener():
    from model.listener_sidecar import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    if LISTENER_ARG in sys.argv[1:]:
        _run_listener()
    elif WORKER_ARG in sys.argv[1:]:
        _run_worker()
    else:
        _run_supervisor()
