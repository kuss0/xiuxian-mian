"""Launch an owned remote Chromium behind a loopback SSH forward."""
import argparse
import json
import os
from pathlib import Path
import re
import select
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
import uuid

CONFIG_ENV = 'XIUXIAN_REMOTE_CHROMIUM_CONFIG'


def read_config(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('remote_chromium_config_invalid')
    if not value['enabled']:
        return value
    host, user, port = value.get('host'), value.get('user', 'root'), value.get('ssh_port', 22)
    ids = value.get('identity_ids')
    key, supervisor = value.get('identity_file'), value.get('supervisor')
    if (not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', host)
            or not isinstance(user, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,31}', user)
            or type(port) is not int or not 1 <= port <= 65535
            or not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids)
            or not isinstance(key, str) or not Path(key).is_absolute() or not Path(key).is_file()
            or not isinstance(supervisor, str) or not supervisor.startswith('/')
            or '\n' in supervisor or '\0' in supervisor
            or value.get('backend', 'mesa') not in {'mesa', 'swiftshader'}):
        raise ValueError('remote_chromium_config_invalid')
    return {**value, 'user': user, 'ssh_port': port}


def ssh_args(config):
    return ['ssh', '-F', '/dev/null', '-p', str(config['ssh_port']), '-i', config['identity_file'],
        '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
        '-o', 'ConnectTimeout=10', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3', '-T']


def save_metadata(path, data):
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
        json.dump(data, stream)
    temporary.replace(path)


def remote_evidence(process):
    if process is None or not hasattr(process, 'remote_owner'):
        return None
    try:
        data = json.loads(process.remote_metadata.read_text())
        if data.get('owner') != process.remote_owner:
            raise ValueError('remote metadata owner mismatch')
    except (OSError, ValueError):
        return {'backend': 'remote', 'cleanup_verified': False}
    return {key: data.get(key) for key in ('backend', 'host', 'identity', 'state',
        'chrome_pid', 'browser_id', 'cleanup_verified', 'reason')}


def verify_remote_cleanup(process):
    evidence = remote_evidence(process)
    if evidence is not None and (evidence.get('state') != 'closed' or evidence.get('cleanup_verified') is not True):
        raise RuntimeError('remote_chromium_cleanup_unverified')
    return evidence


def launch_chrome(browser, chrome, profile, port, *, identity, headless=True, backend='mesa'):
    path = os.environ.get(CONFIG_ENV)
    config = read_config(path) if path else {'enabled': False}
    if not config['enabled'] or identity not in config['identity_ids']:
        return browser.launch_chrome(chrome, profile, port, headless=headless, backend=backend)
    if not headless or browser.cdp_version(port):
        raise RuntimeError('remote_chromium_launch_scope')
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    owner = uuid.uuid4().hex
    metadata = profile / ('remote-' + owner + '.json')
    command = [sys.executable, str(Path(__file__).resolve()), '--config', str(Path(path).resolve()),
        '--identity', str(identity), '--owner', owner, '--metadata', str(metadata),
        f'--remote-debugging-port={port}', f'--user-data-dir={profile}']
    with (profile / 'remote-browser.log').open('a') as log:
        process = subprocess.Popen(command, stdout=log, stderr=log)
    process.remote_owner, process.remote_metadata = owner, metadata
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and process.poll() is None:
            evidence = remote_evidence(process)
            if evidence and evidence.get('state') == 'ready':
                version = browser.cdp_version(port)
                if version and version['webSocketDebuggerUrl'].rsplit('/', 1)[-1] == evidence['browser_id']:
                    return process
            time.sleep(.1)
        raise RuntimeError('remote_chromium_startup_failed')
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--identity', required=True, type=int)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--metadata', type=Path, required=True)
    parser.add_argument('--remote-debugging-port', type=int, required=True)
    parser.add_argument('--user-data-dir', type=Path, required=True)
    args = parser.parse_args()
    config = read_config(args.config)
    if (not config['enabled'] or args.identity not in config['identity_ids']
            or not re.fullmatch('[a-f0-9]{32}', args.owner)
            or not 1024 <= args.remote_debugging_port <= 65535):
        parser.error('remote launch not configured for this identity')
    os.umask(0o077)
    parent_pid = os.getppid()
    if parent_pid <= 1:
        raise RuntimeError('remote_launch_parent_missing')
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    destination = config['user'] + '@' + config['host']
    remote = ['python3', config['supervisor'], '--owner', args.owner,
        '--identity', str(args.identity), '--port', str(args.remote_debugging_port),
        '--backend', config.get('backend', 'mesa')]
    port = args.remote_debugging_port
    control = subprocess.Popen([*ssh_args(config), '-o', 'ExitOnForwardFailure=yes',
        '-L', f'127.0.0.1:{port}:127.0.0.1:{port}', '--', destination, shlex.join(remote)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)
    os.set_blocking(control.stdin.fileno(), False)
    os.set_blocking(control.stdout.fileno(), False)
    buffer = b''
    metadata = {'owner': args.owner, 'backend': 'remote', 'host': config['host'],
        'identity': args.identity, 'state': 'starting', 'cleanup_verified': False}
    save_metadata(args.metadata, metadata)
    last_heartbeat = 0

    def message(kind):
        os.write(control.stdin.fileno(), (json.dumps({'type': kind, 'owner': args.owner}) + '\n').encode())

    def receive(wait):
        nonlocal buffer
        readable, _, _ = select.select([control.stdout], [], [], wait)
        if not readable:
            return []
        chunk = os.read(control.stdout.fileno(), 4096)
        if not chunk:
            return []
        buffer += chunk
        if len(buffer) > 16384:
            raise RuntimeError('remote_control_oversized')
        messages = []
        while b'\n' in buffer:
            line, _, buffer = buffer.partition(b'\n')
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if isinstance(item, dict) and item.get('owner') == args.owner:
                messages.append(item)
        return messages

    try:
        deadline = time.monotonic() + 40
        while not stopped and control.poll() is None:
            if os.getppid() != parent_pid:
                raise RuntimeError('remote_parent_exited')
            now = time.monotonic()
            if now - last_heartbeat >= 2:
                message('heartbeat')
                last_heartbeat = now
            for item in receive(.1):
                if item.get('event') == 'ready':
                    if (metadata['state'] != 'starting' or item.get('port') != args.remote_debugging_port
                            or not isinstance(item.get('browser_id'), str)):
                        raise RuntimeError('remote_control_scope')
                    metadata.update(state='connecting', chrome_pid=item.get('chrome_pid'), browser_id=item['browser_id'])
                elif item.get('event') == 'closed':
                    metadata.update(state='closed', cleanup_verified=item.get('verified') is True,
                                    reason=item.get('reason'))
                    stopped = True
                elif item.get('event') == 'error':
                    raise RuntimeError('remote_supervisor_failed')
            if metadata['state'] == 'connecting':
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{args.remote_debugging_port}/json/version', timeout=.5) as response:
                        version = json.load(response)
                    if version['webSocketDebuggerUrl'].rsplit('/', 1)[-1] != metadata['browser_id']:
                        raise RuntimeError('remote_browser_identity_mismatch')
                    metadata['state'] = 'ready'
                    save_metadata(args.metadata, metadata)
                except (OSError, ValueError):
                    pass
            if metadata['state'] != 'ready' and not stopped and time.monotonic() > deadline:
                raise RuntimeError('remote_bootstrap_timeout')
    except (OSError, RuntimeError) as error:
        metadata['reason'] = type(error).__name__
    finally:
        if not metadata['cleanup_verified']:
            try:
                message('stop')
            except OSError:
                pass
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                for item in receive(.1):
                    if item.get('event') == 'closed':
                        metadata.update(state='closed', cleanup_verified=item.get('verified') is True,
                                        reason=item.get('reason'))
                if metadata['cleanup_verified']:
                    break
                if control.poll() is not None:
                    break
        save_metadata(args.metadata, metadata)
        for process in (control,):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
    return 0 if metadata['cleanup_verified'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
