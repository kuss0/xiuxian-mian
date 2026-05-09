import asyncio
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

    worker = _spawn_worker()
    current_fingerprint = _code_fingerprint()
    pending_fingerprint = None
    pending_since = 0

    try:
        while not stop_requested:
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

            now = time.time()
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
