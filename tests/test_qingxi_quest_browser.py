"""Exercise the real worker lifecycle without a browser or game requests."""
import contextlib
import copy
import json
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tools import qingxi_quest_browser as worker


@pytest.mark.parametrize('mode', ['new', 'joined', 'failed', 'still_stale'])
def test_final_refresh_requeries_only_a_successful_stale_native_join(tmp_path, mode):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for browser JavaScript')
    script = """
const assert=require('node:assert/strict');
let calls=0,clears=0;
global.window={__qx:{player:42,questStatusRevision:7,queue:Promise.resolve(),statusCache:{clear(){clears++}}},
 __quests:{async refresh(){calls++;
  if(mode==='failed')return null;
  if(mode==='new'||mode==='joined'&&calls===2)window.__qx.questStatusRevision++;
  return {ok:true,quests:{states:{qx_q13:{status:'active'}}}};
 }}};
(async()=>{const result=await eval(refresh);
 assert.equal(calls,mode==='joined'||mode==='still_stale'?2:1);
 assert.equal(clears,calls);assert.equal(window.__qx.quiescing,true);
 assert.equal(result.fresh,mode==='new'||mode==='joined');
 assert.equal(result.player,42);assert.equal(result.response?.ok,mode==='failed'?undefined:true);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    path = tmp_path / 'final-refresh.cjs'
    path.write_text('const mode=' + json.dumps(mode) + ',refresh=' + json.dumps(worker.FINAL_REFRESH) + ';\n' + script)
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=10)


@pytest.mark.parametrize('failure', ['', 'entry', 'drain', 'refresh_null', 'stale', 'wrong_player', 'pending'])
def test_main_retains_entry_evidence_and_requires_fresh_final_status(monkeypatch, tmp_path, failure):
    snapshot = {'player': 42, 'error': '', 'pending': 0, 'stored_pending': [], 'requests': 2,
                'casts': 0, 'pending_operations': [], 'receipts': [], 'status_error': '',
                'states': {'qx_q06': {'status': 'ready'}}, 'report': {'error': ''}}
    check = {'player': 42, 'fresh': True, 'response': {'ok': True, 'quests': {'states': snapshot['states']}}}
    page = SimpleNamespace(s='session', loading=True)
    expressions = []

    def js(expression, **kwargs):
        expressions.append(expression)
        if expression == worker.SNAPSHOT:
            return copy.deepcopy(snapshot)
        if expression == worker.FINAL_REFRESH:
            if failure == 'refresh_null':
                snapshot['status_error'] = 'quest_status_unavailable'
                return {'player': 42, 'fresh': False, 'response': None}
            return check
        if expression == 'window.__autobot.report().error':
            return '青溪坊资源加载失败' if failure == 'entry' else ''

    def wait(expression, *args, **kwargs):
        if failure == 'drain' and 'fieldPending' in expression:
            raise TimeoutError('pending receipt')

    if failure == 'stale':
        check['fresh'] = False
    elif failure == 'wrong_player':
        check['response']['playerId'] = 43
    elif failure == 'pending':
        snapshot.update(pending=1, stored_pending=[{'operation_id': 'unknown'}])
    page.js, page.wait = js, wait
    cdp = SimpleNamespace(call=Mock(return_value={'targetId': 'target'}),
                          attach=Mock(return_value='session'), close=Mock())
    process = SimpleNamespace(args=['--remote-debugging-port=1234'], wait=Mock())
    browser = SimpleNamespace(launch_chrome=Mock(return_value=process), find_chrome=Mock(),
        free_port=Mock(return_value=1234), CDP=Mock(return_value=cdp),
        cdp_version=Mock(return_value={'webSocketDebuggerUrl': 'ws://offline'}),
        Page=Mock(return_value=page))
    stop = Mock()
    monkeypatch.setitem(sys.modules, 'longwind_trial_bot', browser)
    monkeypatch.setitem(sys.modules, 'qingxi_local', SimpleNamespace(
        profile_lock=lambda _: contextlib.nullcontext(), read_url_file=lambda _: 'https://offline', stop_browser=stop))
    monkeypatch.setattr(worker, 'browser_script', Mock(return_value=''))
    path = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['worker', '--tool-root', str(tmp_path), '--url-file', str(tmp_path / 'url'),
        '--profile-dir', str(tmp_path / 'profile'), '--result', str(path), '--expected-player-id', '42', '--read-only'])
    code = worker.main()
    result = json.loads(path.read_text())
    assert code == (0 if not failure else 1)
    assert result['player'] == 42 and result['states']['qx_q06']['status'] == 'ready'
    assert result['server_checked'] is (failure in {'', 'entry'})
    assert worker.SNAPSHOT in expressions
    cdp.close.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
    stop.assert_called_once_with(process)
    if failure == 'entry':
        assert result['phase'] == 'held'
        assert '青溪坊资源加载失败' in result['entry_error']
        assert '青溪坊资源加载失败' in result['error']
        assert worker.FINAL_REFRESH in expressions
        assert not result.get('mainline_started_at')
    if failure == 'drain':
        assert result['cleanup_error'] == 'TimeoutError'
        assert worker.FINAL_REFRESH not in expressions
