import asyncio
import os
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MODEL_DIR = PROJECT_ROOT / "model"
WATCH_FILES = (PROJECT_ROOT / "xiuxian.py",)
WORKER_ARG = "--worker"
SCAN_INTERVAL_SEC = 2
RELOAD_STABLE_SEC = 10
WORKER_STOP_TIMEOUT_SEC = 20
LEGACY_PROJECT_ROOTS = (Path("/opt/xiuxian"),)
LEGACY_PROCESS_SCAN_INTERVAL_SEC = 2


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
                        print("代码已稳定且语法检查通过，重启 worker。", flush=True)
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
        _stop_worker(worker)


def _run_worker():
    from model.app import main

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()


if __name__ == '__main__':
    if WORKER_ARG in sys.argv[1:]:
        _run_worker()
    else:
        _run_supervisor()
