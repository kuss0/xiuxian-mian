"""Offline worker lifecycle with native snapshots; no game or browser requests."""
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


def test_native_health_reads_the_current_runtime_and_effective_pause(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for browser JavaScript')
    helper, anchor, _ = worker.NATIVE_HEALTH_HELPER.rpartition('window.__autobot = {')
    assert anchor
    setup = """
const assert = require('node:assert/strict');
const combat = {hp:4525.8,hpMax:15086,paused:false,active:false,fieldPending:0,castOutbox:0,
                enemies:[{hp:5,engaged:false}]};
const world = {environment:{tier:4},draws:10,combat};
const state = {busy:false,running:false};
global.window = {__rt:{mobileHudState:()=>{throw Error('unbound controller must not initialize')}},
                 __fc:{paused:true,isGhost:false,stats:()=>({hp:15086,hpMax:15086})},
                 __qx:{player:42,pending:new Map()},__quests:{snapshot:()=>({world:{current:'yue'}})}};
global.localStorage = {getItem:()=>null};
const readWorld = () => world;
const sceneReady = () => true;
const caveIsSafe = () => world.environment.tier !== 4;
const townIsSafe = () => world.environment.tier === 4;
const caveSettlementPending = () => false;
"""
    checks = """
const first = window.__qingxiNativeHealth.snapshot();
assert.equal(first.hp,4525.8);
assert.equal(first.paused,true);
assert.equal(first.pending,0);
assert.equal(first.world_current,'yue');
assert.equal(first.combat_matches_runtime,false);
window.__fc.stats=()=>combat;
window.__fc.paused=false;
window.__rt.mobileHudState=()=>({blocked:true,portrait:false});
const overlay=window.__qingxiNativeHealth.snapshot();
assert.equal(overlay.combat_matches_runtime,true);
assert.equal(overlay.paused,false);
assert.equal(overlay.runtime_blocked,true);
window.__rt.mobileHudState=()=>({blocked:false,portrait:false});
assert.equal(window.__qingxiNativeHealth.snapshot().runtime_blocked,false);
world.environment.tier=1;
world.combat=null;
window.__quests=null;
window.__rt={mobileHudState:()=>{throw Error('missing controller must not initialize')}};
const cave=window.__qingxiNativeHealth.snapshot();
assert.equal(cave.hp,undefined);
assert.equal(cave.last_known_world,'yue');
assert.notEqual(cave.runtime_id,first.runtime_id);
world.combat=undefined;
assert.equal(window.__qingxiNativeHealth.snapshot().combat_present,null);
"""
    path = tmp_path / 'native-health.cjs'
    path.write_text(setup + helper + checks)
    subprocess.run([node, str(path)], check=True, capture_output=True, text=True, timeout=10)


def health_snapshot(**changes):
    snapshot = dict(
        player=42, error='', status_error='', cast_error='', pending=0,
        pending_operations=[], stored_pending=[], scene_ready=True,
        cave_safe=False, town_safe=True, scene_tier=4, world_current='yue',
        position=[2.55, 0, .7], draws=10, runtime_id=1, combat_id=1,
        control_mode='companion', mode='walk', runtime_blocked=False,
        paused=False, ghost=False, flight_active=False, chariot_mounted=False,
        combat_present=True, combat_matches_runtime=True, combat_active=False, overworld=False, state='ready',
        engaged_enemies=0, combat_settlement_pending=False,
        hp=4525.8, hp_max=15086, home=None,
        automation_busy=False, automation_running=False,
    )
    snapshot.update(changes)
    return snapshot


def run_worker(monkeypatch, tmp_path, snapshots, *, transition_failure=None, **flags):
    """Run main(), including the real health gate and final refresh."""
    clock = SimpleNamespace(now=0)
    def sleep(seconds):
        clock.now += seconds if seconds <= 2 else 305
    monkeypatch.setattr(worker, 'time', SimpleNamespace(
        sleep=sleep, monotonic=lambda: clock.now, time=lambda: 1795239469 + clock.now))
    final = dict(player=42, error='', status_error='', casts=0, requests=2,
                 pending=0, pending_operations=[], stored_pending=[], receipts=[],
                 states={'qx_q11': {'status': 'active', 'progress': {'arrows': 2}}},
                 combat_active=False, report={'error': ''}, run={'done': True})
    reads, actions = [], []
    sequence = iter(snapshots)
    current = copy.deepcopy(snapshots[0])
    def js(expression, **kwargs):
        nonlocal current
        if expression == worker.HEALTH_SNAPSHOT:
            current = copy.deepcopy(next(sequence, current))
            reads.append(copy.deepcopy(current))
            return copy.deepcopy(current)
        if expression in (worker.RETURN_CAVE_START, worker.TOWN_REENTRY_START):
            actions.append(expression)
            if transition_failure is not None:
                transition_failure(expression, clock, kwargs.get('timeout'))
            return None
        if expression in (worker.RETURN_CAVE_DONE, worker.TOWN_REENTRY_DONE):
            if transition_failure is not None:
                transition_failure(expression, clock, kwargs['timeout'])
            return {'done': True, 'error': '', 'started_at': 1795239469000,
                    'value': '已返回洞府' if expression == worker.RETURN_CAVE_DONE else '已进入青溪坊'}
        if expression == worker.SNAPSHOT:
            return copy.deepcopy(final)
        if expression == worker.FINAL_REFRESH:
            return {'player': 42, 'fresh': True,
                    'response': {'ok': True, 'quests': {'states': final['states']}}}
        if expression == 'window.__autobot.report().error':
            return ''
        if 'window.__autobot.mainline(' in expression:
            actions.append('mainline')
            assert current['hp'] / current['hp_max'] >= .8
            assert current['town_safe'] is True
            assert current['combat_active'] is False
            assert current['pending_operations'] == current['stored_pending'] == []
        return None
    page = SimpleNamespace(s='session', loading=True, js=js, wait=Mock(), keep_visible=Mock(return_value=True))
    cdp = SimpleNamespace(call=Mock(return_value={'targetId': 'target'}),
                          attach=Mock(return_value='session'), close=Mock())
    process = SimpleNamespace(args=['--remote-debugging-port=1234'], wait=Mock())
    browser = SimpleNamespace(launch_chrome=Mock(return_value=process), find_chrome=Mock(),
        free_port=Mock(return_value=1234), CDP=Mock(return_value=cdp),
        cdp_version=Mock(return_value={'webSocketDebuggerUrl': 'ws://offline'}),
        Page=Mock(return_value=page))
    monkeypatch.setitem(sys.modules, 'longwind_trial_bot', browser)
    monkeypatch.setitem(sys.modules, 'qingxi_local', SimpleNamespace(
        profile_lock=lambda _: contextlib.nullcontext(), read_url_file=lambda _: 'https://offline',
        stop_browser=Mock()))
    monkeypatch.setattr(worker, 'browser_script', Mock(return_value=''))
    result_path = tmp_path / 'result.json'
    argv = ['worker', '--tool-root', str(tmp_path), '--url-file', str(tmp_path / 'url'),
            '--profile-dir', str(tmp_path / 'profile'), '--result', str(result_path),
            '--expected-player-id', '42']
    argv.extend('--' + key.replace('_', '-') for key, enabled in flags.items() if enabled)
    monkeypatch.setattr(sys, 'argv', argv)
    code = worker.main()
    return code, json.loads(result_path.read_text()), reads, actions


def test_native_recovery_gates_mainline_after_real_health_increase(monkeypatch, tmp_path):
    snapshots = [health_snapshot(), health_snapshot(hp=9000, draws=20),
                 health_snapshot(hp=12500, draws=30)]
    code, result, reads, actions = run_worker(monkeypatch, tmp_path, snapshots)
    assert code == 0
    assert result['health_recovery']['phase'] == 'ready'
    assert reads[-1]['hp'] / reads[-1]['hp_max'] >= .8
    assert reads[-1]['draws'] > reads[0]['draws']
    assert actions == ['mainline']
    assert result['mainline_started_at'] > result['entry_started_at']
    assert result['states']['qx_q11']['progress']['arrows'] == 2


@pytest.mark.parametrize('read_timeout', [False, True])
def test_native_recovery_returns_and_reenters_through_verified_home(monkeypatch, tmp_path, read_timeout):
    cave = dict(cave_safe=True, town_safe=False, scene_tier=1, world_current=None,
                runtime_id=2, combat_id=None, combat_present=False, combat_matches_runtime=None,
                combat_active=False, paused=None, ghost=None, hp=None, hp_max=None,
                home={'destination': 'cave', 'verified': True, 'at': 1795239469001},
                last_known_world='yue')
    snapshots = [
        health_snapshot(town_safe=False, position=[500, 0, 500], combat_active=True,
                        overworld=True, state='fighting'),
        health_snapshot(**cave),
        health_snapshot(runtime_id=3, combat_id=3, draws=20),
        health_snapshot(runtime_id=3, combat_id=3, hp=9000, draws=30),
        health_snapshot(runtime_id=3, combat_id=3, hp=12500, draws=40),
    ]
    timed_out = set()
    def transition_failure(expression, clock, timeout):
        if timeout is not None and expression not in timed_out:
            timed_out.add(expression)
            clock.now += timeout
            raise TimeoutError('Runtime.evaluate exceeded 30s')

    code, result, reads, actions = run_worker(
        monkeypatch, tmp_path, snapshots, transition_failure=transition_failure if read_timeout else None)
    assert code == 0
    assert result['health_recovery']['phase'] == 'ready'
    assert actions == [worker.RETURN_CAVE_START, worker.TOWN_REENTRY_START, 'mainline']
    assert any(snapshot['home'] and snapshot['home']['verified'] is True for snapshot in reads)
    if read_timeout:
        assert timed_out == {worker.RETURN_CAVE_DONE, worker.TOWN_REENTRY_DONE}
        transitions = result['health_recovery']['transitions']
        assert all(transitions[name]['read_timeouts'] == 1 for name in ('return', 'reentry'))


def test_native_return_read_timeouts_use_remaining_transition_budget(monkeypatch, tmp_path):
    snapshot = health_snapshot(town_safe=False, position=[500, 0, 500], combat_active=True,
                               overworld=True, state='fighting')
    calls = []
    def transition_failure(expression, clock, timeout):
        if expression == worker.RETURN_CAVE_DONE:
            calls.append((clock.now, timeout))
            clock.now += timeout
            raise TimeoutError('Runtime.evaluate exceeded 30s')

    code, result, _, actions = run_worker(monkeypatch, tmp_path, [snapshot], transition_failure=transition_failure)
    assert code == 1
    assert actions == [worker.RETURN_CAVE_START]
    assert 'mainline_started_at' not in result
    assert result['health_recovery']['reason'] == 'return_timeout'
    transition = result['health_recovery']['transitions']['return']
    assert transition['stage'] == 'waiting_receipt'
    assert transition['read_timeouts'] == len(calls) > 1
    first, last = calls[0], calls[-1]
    assert first[1] == 30
    assert last[0] + last[1] == first[0] + worker.QUEST_HOME_SEC
    assert 0 < last[1] < first[1]


@pytest.mark.parametrize(('failure_expression', 'error', 'reason', 'stage'), [
    (worker.RETURN_CAVE_START, TimeoutError('Runtime.evaluate exceeded 30s'), 'return_timeout', 'dispatch'),
    (worker.RETURN_CAVE_DONE, ConnectionError('CDP disconnected'), 'return_error', 'waiting_receipt'),
    (worker.RETURN_CAVE_DONE, TimeoutError('socket send timed out'), 'return_error', 'waiting_receipt'),
])
def test_native_return_remains_held_on_dispatch_timeout_or_transport_error(
        monkeypatch, tmp_path, failure_expression, error, reason, stage):
    snapshot = health_snapshot(town_safe=False, position=[500, 0, 500], combat_active=True,
                               overworld=True, state='fighting')
    attempted = []
    def transition_failure(expression, clock, timeout):
        attempted.append(expression)
        if expression == failure_expression:
            raise error

    code, result, _, actions = run_worker(monkeypatch, tmp_path, [snapshot], transition_failure=transition_failure)
    assert code == 1
    assert attempted.count(failure_expression) == 1
    assert attempted[-1] == failure_expression
    assert actions == [worker.RETURN_CAVE_START]
    assert 'mainline_started_at' not in result
    recovery = result['health_recovery']
    assert recovery['reason'] == reason
    assert recovery['exception'] == type(error).__name__
    assert recovery['transitions']['return'] == {'stage': stage, 'read_timeouts': 0}


def test_native_recovery_does_not_use_stale_home_verification(monkeypatch, tmp_path):
    snapshots = [
        health_snapshot(town_safe=False, position=[500, 0, 500], combat_active=True,
                        overworld=True, state='fighting'),
        health_snapshot(cave_safe=True, town_safe=False, scene_tier=1,
                        home={'destination': 'cave', 'verified': True, 'at': 1795239468999}),
    ]
    code, result, _, actions = run_worker(monkeypatch, tmp_path, snapshots)
    assert code == 1
    assert result['health_recovery']['reason'] == 'return_unverified'
    assert actions == [worker.RETURN_CAVE_START]
    assert 'mainline_started_at' not in result


def test_native_recovery_does_not_accept_an_unexplained_cave_start(monkeypatch, tmp_path):
    snapshot = health_snapshot(cave_safe=True, town_safe=False, scene_tier=1,
                               combat_present=False, combat_matches_runtime=None,
                               combat_id=None, paused=None, ghost=None, hp=None, hp_max=None)
    code, result, _, actions = run_worker(monkeypatch, tmp_path, [snapshot])
    assert code == 1
    assert result['health_recovery']['phase'] == 'blocked'
    assert actions == []
    assert not result.get('mainline_started_at')


def test_native_recovery_blocks_an_unsafe_town_after_reentry(monkeypatch, tmp_path):
    snapshots = [
        health_snapshot(town_safe=False, position=[500, 0, 500], combat_active=True,
                        overworld=True, state='fighting'),
        health_snapshot(cave_safe=True, town_safe=False, scene_tier=1,
                        home={'destination': 'cave', 'verified': True, 'at': 1795239469001}),
        health_snapshot(town_safe=False, position=[500, 0, 500], hp=12500, draws=20),
    ]
    code, result, _, actions = run_worker(monkeypatch, tmp_path, snapshots)
    assert code == 1
    assert result['health_recovery']['reason'] == 'reentry_unverified'
    assert actions == [worker.RETURN_CAVE_START, worker.TOWN_REENTRY_START]
    assert 'mainline_started_at' not in result


def test_native_recovery_does_not_start_after_the_wait_deadline(monkeypatch, tmp_path):
    snapshots = [health_snapshot(draws=n) for n in (10, 20, 30)]
    snapshots.append(health_snapshot(hp=12500, draws=40))
    code, result, _, actions = run_worker(monkeypatch, tmp_path, snapshots)
    assert code == 1
    assert result['health_recovery']['reason'] == 'recovery_timeout'
    assert actions == []
    assert 'mainline_started_at' not in result


@pytest.mark.parametrize('taint', [
    'paused', 'combat', 'combat_started', 'memory_pending', 'disk_pending', 'ghost', 'dead',
    'flight', 'chariot', 'no_combat_runtime', 'identity', 'scene',
    'native_draws_stalled', 'native_hp_stalled', 'automation_busy',
    'runtime_changed', 'profile_changed', 'settlement_pending', 'stale_combat',
])
def test_native_recovery_blocks_mainline_and_retains_evidence(monkeypatch, tmp_path, taint):
    snapshot = health_snapshot()
    if taint == 'paused':
        snapshot['paused'] = True
    elif taint == 'combat':
        snapshot.update(town_safe=False, combat_active=True, engaged_enemies=1,
                        overworld=True, state='fighting')
    elif taint == 'memory_pending':
        snapshot['pending_operations'] = [{'action': 'field-defeat'}]
    elif taint == 'disk_pending':
        snapshot['stored_pending'] = [{'action': 'field-defeat'}]
    elif taint == 'ghost':
        snapshot['ghost'] = True
    elif taint == 'dead':
        snapshot['hp'] = 0
    elif taint == 'flight':
        snapshot['flight_active'] = True
    elif taint == 'chariot':
        snapshot['chariot_mounted'] = True
    elif taint == 'no_combat_runtime':
        snapshot.update(hp=None, hp_max=None, paused=None, ghost=None)
    elif taint == 'identity':
        snapshot['player'] = 43
    elif taint == 'scene':
        snapshot['scene_ready'] = False
    elif taint == 'automation_busy':
        snapshot['automation_busy'] = True
    elif taint == 'settlement_pending':
        snapshot['combat_settlement_pending'] = True
    elif taint == 'stale_combat':
        snapshot['combat_matches_runtime'] = False
    second = copy.deepcopy(snapshot)
    if taint == 'native_draws_stalled':
        second['hp'] = 12500
    elif taint == 'native_hp_stalled':
        second['draws'] = 20
    elif taint == 'combat_started':
        second.update(town_safe=False, combat_active=True, engaged_enemies=1,
                      overworld=True, state='fighting', draws=20, hp=12500)
    elif taint == 'runtime_changed':
        second.update(runtime_id=2, draws=20, hp=12500)
    elif taint == 'profile_changed':
        second.update(hp_max=5000, draws=20)
    code, result, _, actions = run_worker(monkeypatch, tmp_path, [snapshot, second])
    assert code == 1
    assert result['phase'] == 'held'
    assert result['health_recovery']['phase'] == 'blocked'
    assert 'qingxi_native_health_' in result['error']
    assert not result.get('mainline_started_at')
    assert 'mainline' not in actions
    assert result['server_checked'] is True


def test_read_only_inspection_does_not_start_native_recovery(monkeypatch, tmp_path):
    code, result, reads, actions = run_worker(monkeypatch, tmp_path, [health_snapshot()], read_only=True)
    assert code == 0
    assert result['phase'] == 'inspected'
    assert reads == actions == []
    assert not result.get('mainline_started_at')
