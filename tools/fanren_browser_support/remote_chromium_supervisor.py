"""Standalone SSH-owned Chromium supervisor; contains no game or account code."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import time
import urllib.request

OWNER_ENV = 'XIUXIAN_REMOTE_CHROMIUM_OWNER'


def emit(event, owner, **fields):
    print(json.dumps({'event': event, 'owner': owner, **fields}), flush=True)


def owned_processes(owner, group_id=None):
    marker = (OWNER_ENV + '=' + owner).encode()
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        handle = None
        try:
            before = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            # Chromium can rewrite its environment after exec. Its new session
            # still belongs to the exact child we created, including renderers.
            in_group = group_id is not None and before[2] == before[3] == str(group_id)
            if before[0] == 'Z' or not (in_group or marker in (entry / 'environ').read_bytes().split(b'\0')):
                continue
            handle = os.pidfd_open(int(entry.name))
            after = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if before[19] != after[19]:
                os.close(handle)
                continue
            found.append(handle)
        except OSError:
            if handle is not None:
                os.close(handle)
    return found


def signal_owned(owner, sig, group_id=None):
    handles = owned_processes(owner, group_id)
    for handle in handles:
        try:
            signal.pidfd_send_signal(handle, sig)
        except ProcessLookupError:
            pass
        finally:
            os.close(handle)
    return len(handles)


def remaining(owner, group_id=None):
    handles = owned_processes(owner, group_id)
    for handle in handles:
        os.close(handle)
    return len(handles)


def close_owned(process, owner):
    signal_owned(owner, signal.SIGTERM, process.pid)
    deadline = time.monotonic() + 4
    while (process.poll() is None or remaining(owner, process.pid)) and time.monotonic() < deadline:
        process.poll()
        time.sleep(.05)
    if remaining(owner, process.pid):
        signal_owned(owner, signal.SIGKILL, process.pid)
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        return False
    return remaining(owner, process.pid) == 0


def chrome_args(chrome, profile, port, backend):
    args = [chrome, '--remote-debugging-address=127.0.0.1', f'--remote-debugging-port={port}',
        f'--user-data-dir={profile}', '--no-first-run', '--no-default-browser-check',
        '--window-size=640,480', '--disable-session-crashed-bubble',
        '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
        '--disable-background-timer-throttling', '--disable-background-networking',
        '--disable-component-update', '--disable-sync', '--disable-dev-shm-usage',
        '--headless=new', '--force-device-scale-factor=0.5']
    args += (['--use-gl=angle', '--use-angle=gl-egl', '--ignore-gpu-blocklist'] if backend == 'mesa'
             else ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
    if os.geteuid() == 0:
        args.append('--no-sandbox')
    return [*args, 'about:blank']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--owner', required=True)
    parser.add_argument('--identity', type=int, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--backend', choices=['mesa', 'swiftshader'], default='mesa')
    parser.add_argument('--root', type=Path, default=Path('/var/lib/xiuxian-browser-worker'))
    parser.add_argument('--chrome')
    parser.add_argument('--lease-seconds', type=float, default=20)
    args = parser.parse_args()
    if (not re.fullmatch('[a-f0-9]{32}', args.owner) or args.identity <= 0
            or not 1024 <= args.port <= 65535 or not .2 <= args.lease_seconds <= 60):
        parser.error('invalid worker scope')
    chrome = args.chrome or shutil.which('chromium') or shutil.which('google-chrome')
    if not chrome:
        emit('error', args.owner, reason='chromium_missing')
        return 1
    os.umask(0o077)
    profile = args.root / 'profiles' / str(args.identity)
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    process = None
    reason, verified = 'startup_failed', False
    with (profile / '.worker.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            emit('error', args.owner, reason='identity_profile_busy')
            return 1
        try:
            with socket.socket() as probe:
                probe.bind(('127.0.0.1', args.port))
            with (profile / ('browser-' + args.owner + '.log')).open('a') as log:
                process = subprocess.Popen(chrome_args(chrome, profile, args.port, args.backend),
                    stdout=log, stderr=log, start_new_session=True,
                    env={**os.environ, OWNER_ENV: args.owner})
            deadline = time.monotonic() + 25
            version = None
            while not stopped and time.monotonic() < deadline and process.poll() is None:
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{args.port}/json/version', timeout=1) as response:
                        version = json.load(response)
                    break
                except (OSError, ValueError):
                    time.sleep(.1)
            if version is None:
                raise RuntimeError('cdp_startup_failed')
            emit('ready', args.owner, port=args.port, chrome_pid=process.pid,
                 browser_id=version['webSocketDebuggerUrl'].rsplit('/', 1)[-1], backend=args.backend)
            heartbeat = time.monotonic()
            buffer = b''
            reason = 'browser_exit'
            while not stopped and process.poll() is None:
                if time.monotonic() - heartbeat > args.lease_seconds:
                    reason = 'lease_expired'
                    break
                readable, _, _ = select.select([0], [], [], .2)
                if not readable:
                    continue
                line = os.read(0, 4096)
                if not line:
                    reason = 'control_eof'
                    break
                buffer += line
                if len(buffer) > 16384:
                    reason = 'invalid_control'
                    break
                while b'\n' in buffer:
                    chunk, _, buffer = buffer.partition(b'\n')
                    try:
                        message = json.loads(chunk)
                    except ValueError:
                        reason = 'invalid_control'
                        stopped = True
                        break
                    if (not isinstance(message, dict) or message.get('owner') != args.owner
                            or message.get('type') not in {'heartbeat', 'stop'}):
                        reason = 'invalid_control'
                        stopped = True
                        break
                    if message['type'] == 'stop':
                        reason = 'requested_stop'
                        stopped = True
                        break
                    heartbeat = time.monotonic()
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            emit('error', args.owner, reason=type(error).__name__)
        finally:
            if process is not None:
                verified = close_owned(process, args.owner)
                emit('closed', args.owner, verified=verified, reason=reason)
    return 0 if verified else 1


if __name__ == '__main__':
    raise SystemExit(main())
