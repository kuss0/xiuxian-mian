import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools.fanren_browser_support import remote_chromium as remote

SUPERVISOR = Path(__file__).resolve().parents[1] / 'tools/fanren_browser_support/remote_chromium_supervisor.py'
FAKE_CHROME = r'''#!/usr/bin/env python3
import json,os,subprocess,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
if os.environ.get('REMOTE_TEST_SCRUB_OWNER') and 'XIUXIAN_REMOTE_CHROMIUM_OWNER' in os.environ:
 env={k:v for k,v in os.environ.items() if k!='XIUXIAN_REMOTE_CHROMIUM_OWNER'}
 os.execve(sys.executable,[sys.executable,__file__,*sys.argv[1:]],env)
port=int(next(x.split('=',1)[1] for x in sys.argv if x.startswith('--remote-debugging-port=')))
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
with open(os.environ['REMOTE_TEST_CHILD_FILE'],'w') as f:f.write(str(child.pid))
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  body=json.dumps({'webSocketDebuggerUrl':f'ws://127.0.0.1:{port}/devtools/browser/offline-fixture'}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',port),Handler).serve_forever()
'''


def config(tmp_path):
    key = tmp_path / 'key'
    key.write_text('offline fixture, not an SSH key')
    return {'enabled': True, 'host': '100.119.30.39', 'user': 'root', 'ssh_port': 32123,
        'identity_ids': [42], 'identity_file': str(key), 'supervisor': '/opt/worker/supervisor.py'}


@pytest.mark.parametrize('change', [
    {'host': '-oProxyCommand=bad'}, {'host': 'host;command'}, {'user': 'root;command'},
    {'ssh_port': True}, {'ssh_port': 0}, {'identity_ids': [True]}, {'identity_ids': ['42']},
    {'supervisor': '/opt/worker\ncommand'}, {'backend': 'unknown'},
])
def test_invalid_remote_config_cannot_become_an_ssh_command(tmp_path, change):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({**config(tmp_path), **change}))
    with pytest.raises(ValueError, match='remote_chromium_config_invalid'):
        remote.read_config(path)


def test_identity_selection_does_not_launch_remote_for_another_account(tmp_path, monkeypatch):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config(tmp_path)))
    monkeypatch.setenv(remote.CONFIG_ENV, str(path))
    browser = SimpleNamespace(launch_chrome=Mock(return_value='local'))
    assert remote.launch_chrome(browser, 'chrome', tmp_path, 40000, identity=43) == 'local'
    browser.launch_chrome.assert_called_once()


@pytest.mark.parametrize('owner,state,verified', [('wrong', 'closed', True), ('owned', 'ready', True),
                                                ('owned', 'closed', False)])
def test_remote_exit_needs_the_matching_owner_and_closed_receipt(tmp_path, owner, state, verified):
    path = tmp_path / 'receipt.json'
    path.write_text(json.dumps({'owner': owner, 'state': state, 'cleanup_verified': verified}))
    process = SimpleNamespace(remote_owner='owned', remote_metadata=path)
    with pytest.raises(RuntimeError, match='remote_chromium_cleanup_unverified'):
        remote.verify_remote_cleanup(process)


def read_event(process, timeout=5):
    if not select.select([process.stdout], [], [], timeout)[0]:
        raise AssertionError('Supervisor did not produce an event')
    return json.loads(process.stdout.readline())


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0] != 'Z'
    except FileNotFoundError:
        return False


def launch_supervisor(tmp_path, owner='a' * 32, scrub=False):
    chrome = tmp_path / 'fake-chrome'
    chrome.write_text(FAKE_CHROME)
    chrome.chmod(0o700)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    child_file = tmp_path / ('child-' + owner)
    process = subprocess.Popen([sys.executable, str(SUPERVISOR), '--root', str(tmp_path / 'profiles'),
        '--chrome', str(chrome), '--owner', owner, '--identity', '42', '--port', str(port),
        '--lease-seconds', '.5'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, 'REMOTE_TEST_CHILD_FILE': str(child_file),
             'REMOTE_TEST_SCRUB_OWNER': '1' if scrub else ''})
    return process, child_file


@pytest.mark.skipif(not hasattr(os, 'pidfd_open'), reason='Linux pidfd required')
@pytest.mark.parametrize('ending', ['stop', 'eof', 'lease', 'signal', 'wrong_owner'])
@pytest.mark.parametrize('scrub', [False, True])
def test_supervisor_reaps_chrome_and_children_on_every_exit(tmp_path, ending, scrub):
    process, child_file = launch_supervisor(tmp_path, scrub=scrub)
    ready = read_event(process)
    assert ready['event'] == 'ready'
    child_pid = int(child_file.read_text())
    try:
        if ending in {'stop', 'wrong_owner'}:
            owner = 'b' * 32 if ending == 'wrong_owner' else 'a' * 32
            process.stdin.write((json.dumps({'type': 'stop', 'owner': owner}) + '\n').encode())
            process.stdin.flush()
        elif ending == 'eof':
            process.stdin.close()
        elif ending == 'signal':
            process.send_signal(signal.SIGTERM)
        closed = read_event(process)
        assert closed['event'] == 'closed' and closed['owner'] == 'a' * 32
        assert closed['verified'] is True
        assert process.wait(timeout=5) == 0
        assert not alive(ready['chrome_pid']) and not alive(child_pid)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


@pytest.mark.skipif(not hasattr(os, 'pidfd_open'), reason='Linux pidfd required')
def test_second_supervisor_cannot_take_over_an_owned_identity(tmp_path):
    first, _ = launch_supervisor(tmp_path)
    ready = read_event(first)
    second, _ = launch_supervisor(tmp_path, owner='b' * 32)
    try:
        rejected = read_event(second)
        assert rejected['reason'] == 'identity_profile_busy'
        assert second.wait(timeout=5) == 1
        assert alive(ready['chrome_pid'])
    finally:
        first.stdin.write((json.dumps({'type': 'stop', 'owner': 'a' * 32}) + '\n').encode())
        first.stdin.flush()
        assert read_event(first)['verified'] is True
        first.wait(timeout=5)
