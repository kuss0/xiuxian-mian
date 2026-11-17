import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import qingxi_quests as quests


def data(states, **extra):
    return {'ok': True, 'quests': {'world': {'current': 'yue'}, 'states': states, **extra}}


def test_catalog_includes_all_official_regions_and_original_requirements():
    rules = quests.catalog()
    q = {q['id']: q for q in rules['quests']}
    assert len(q) == 79
    assert {v['region'] for v in q.values()} == {'yue', 'jiuguo', 'tiandao'}
    assert len(rules['sources']) == 9
    assert q['qx_q04']['objectives'][0]['required'] == 20
    assert q['qx_q05']['objectives'][0]['required'] == 30
    assert q['qx_q06']['objectives'][0]['type'] == 'catch'
    assert 'trial' not in {o['type'] for v in q.values() for o in v['objectives']}


def test_intro_progresses_accept_talk_turn_in_and_delivery_with_receipts():
    rules = copy.deepcopy(quests.catalog())
    rules['quests'] = rules['quests'][:2]
    calls, record, durable = [], {}, []
    states = {'qx_q00': {'status': 'available'}}

    async def call(action, **payload):
        calls.append((action, payload))
        if action == 'accept':
            assert durable[-1]['pending']['operationId'] == payload['operationId']
            states[payload['questId']] = {'status': 'active' if payload['questId'] == 'qx_q00' else 'ready'}
        if action == 'talk':
            states['qx_q00'] = {'status': 'ready', 'progress': {'registration': 1}}
        if action == 'turn-in':
            states[payload['questId']]['status'] = 'rewarded'
            if payload['questId'] == 'qx_q00':
                states['qx_q01'] = {'status': 'available'}
        reply = data(copy.deepcopy(states))
        if action == 'turn-in':
            reply['grants'] = [{'name': '灵石', 'quantity': 25}]
        return reply

    asyncio.run(quests.run_protocol(call, record, lambda: durable.append(copy.deepcopy(record)), rules=rules))
    assert [a for a, _ in calls] == ['status', 'accept', 'talk', 'turn-in', 'accept', 'turn-in']
    assert record['phase'] == 'done'
    assert len(record['receipts']) == 5
    assert record['observed']['qx_q01']['status'] == 'rewarded'
    assert all('pending' in r for r in durable if r.get('phase') == 'running' and 'pending' in r)
    assert len({p['operationId'] for a, p in calls if a != 'status'}) == 5


def test_gather_uses_server_node_duration_and_scaled_quantity():
    response = data({'qx_q02': {'status': 'active', 'required': {'herbs': 1}}},
                    nodes=[{'id': 'herb:1', 'itemId': 'qxq_fresh_herb', 'position': [62, 78]}])
    finish = data({'qx_q02': {'status': 'ready', 'progress': {'herbs': 1}, 'required': {'herbs': 1}}})
    call = AsyncMock(side_effect=[response, {**response, 'gatherId': 'g1', 'duration': 8}, finish])
    sleeper, record = AsyncMock(), {}
    asyncio.run(quests.run_protocol(call, record, Mock(), sleeper=sleeper, max_actions=1))
    sleeper.assert_awaited_once_with(8.5)
    assert [c.args[0] for c in call.await_args_list] == ['status', 'gather-start', 'gather-finish']
    assert call.await_args_list[-1].kwargs['gatherId'] == 'g1'
    assert call.await_args_list[-1].kwargs['position'] == [62, 0, 78]
    assert not record.get('gather') and not record.get('pending')


def test_combat_is_accepted_but_never_forged_or_counted_as_trial():
    action, gaps = quests.plan_next(data({'qx_q04': {'status': 'active', 'progress': {'bandits': 2},
                                                  'required': {'bandits': 24}}})['quests'], quests.catalog())
    assert action is None
    assert gaps[0]['progress'] == 2 and gaps[0]['required'] == 24
    assert gaps[0]['reason'] == 'gameplay_required'


def test_ready_reward_choice_requires_explicit_configuration():
    state = data({'qx_q21': {'status': 'ready'}})['quests']
    action, gaps = quests.plan_next(state, quests.catalog())
    assert action is None and gaps[0]['reason'] == 'reward_choice_required'
    action, _ = quests.plan_next(state, quests.catalog(), {'qx_q21': 'release'})
    assert action['action'] == 'turn-in' and action['choice'] == 'release'


@pytest.mark.parametrize('failure', ['timeout', 'bad_receipt', 'unchanged_receipt'])
def test_uncertain_mutation_retains_durable_intent_and_never_retries(failure):
    initial = data({'qx_q00': {'status': 'available'}})
    second = RuntimeError('http_timeout') if failure == 'timeout' else (
        {'ok': True} if failure == 'bad_receipt' else initial)
    record, snapshots = {}, []
    call = AsyncMock(side_effect=[initial, second])
    with pytest.raises(RuntimeError):
        asyncio.run(quests.run_protocol(call, record, lambda: snapshots.append(copy.deepcopy(record))))
    assert call.await_count == 2
    assert record['pending']['action'] == 'accept'
    assert record['pending']['operationId'] == snapshots[-1]['pending']['operationId']
    call = AsyncMock(return_value=initial)
    with pytest.raises(RuntimeError, match='pending_requires_review'):
        asyncio.run(quests.run_protocol(call, record, Mock()))
    assert call.await_count == 1


def test_refresh_keeps_pending_and_never_mutates_game():
    record = {'phase': 'held', 'pending': {'action': 'turn-in', 'operationId': 'old'}}
    call = AsyncMock(return_value=data({'qx_q00': {'status': 'rewarded'}}))
    asyncio.run(quests.run_protocol(call, record, Mock(), status_only=True))
    call.assert_awaited_once_with('status')
    assert record['phase'] == 'held' and record['pending']['operationId'] == 'old'


def test_real_rewarded_status_reconciles_lost_turn_in_without_resending():
    # Real first-task status from Baji's 2026-11-16 turn-in receipt.
    record = {'phase': 'held', 'pending': {'action': 'turn-in', 'questId': 'qx_q00', 'operationId': 'old'}}
    call = AsyncMock(return_value=data({'qx_q00': {'status': 'rewarded', 'progress': {'registration': 1}},
                                        'qx_q01': {'status': 'available'}}))
    asyncio.run(quests.run_protocol(call, record, Mock(), status_only=True))
    call.assert_awaited_once_with('status')
    assert record['phase'] == 'idle' and not record.get('pending')
    assert record['receipts'][0]['operation_id'] == 'old'
    assert record['receipts'][0]['reconciled_from_status']
    assert record['receipts'][0]['grants'] == []  # Never invent unseen rewards.


def test_duplicate_nonprogressing_gather_is_stopped():
    response = data({'qx_q02': {'status': 'active'}},
                    nodes=[{'id': 'herb:1', 'itemId': 'qxq_fresh_herb', 'position': [62, 78]}])
    call = AsyncMock(side_effect=[response, {**response, 'gatherId': 'g1', 'duration': 8}, response])
    with pytest.raises(RuntimeError, match='no_progress'):
        asyncio.run(quests.run_protocol(call, {}, Mock(), sleeper=AsyncMock()))
    assert call.await_count == 3


def test_other_region_and_puzzle_nodes_are_not_automatically_used():
    response = data({'qx_q02': {'status': 'active'}}, nodes=[
        {'id': 'h1', 'itemId': 'qxq_fresh_herb', 'position': [1, 2], 'region': 'jiuguo'},
        {'id': 'h2', 'itemId': 'qxq_fresh_herb', 'position': [1, 2], 'puzzle': 'test'},
    ])
    action, gaps = quests.plan_next(response['quests'], quests.catalog())
    assert action is None and gaps[0]['reason'] == 'live_node_unavailable'


def test_later_story_gaps_are_visible_and_never_counted_as_all_done():
    response = data({'future_q99': {'status': 'available'}})
    call, record = AsyncMock(return_value=response), {}
    asyncio.run(quests.run_protocol(call, record, Mock()))
    assert record['phase'] == 'waiting_gameplay'
    assert record['gaps'][0]['reason'] == 'catalog_not_supported'
    assert 'future_q99' in record['observed']


def test_activity_ownership_blocks_concurrent_trials_but_not_self(monkeypatch):
    from model import activity_occupancy
    monkeypatch.setattr(activity_occupancy, 'get_expedition_run_state',
                        lambda: {'qingxi_quests': {'99': {'phase': 'running'}}})
    monkeypatch.setattr(activity_occupancy, 'get_hunt_run_state', lambda: {})
    monkeypatch.setattr(activity_occupancy, 'get_replica_run_state', lambda: {})
    assert activity_occupancy.get_activity_block_reason(99) == '青溪坊任务执行中或结果待核对'
    assert not activity_occupancy.get_activity_block_reason(99, include_qingxi=False)


@pytest.mark.parametrize('uncertain', [
    {'gameplay_uncertain': True}, {'pending': {'operationId': 'old'}},
    {'gather': {'gatherId': 'old'}},
])
def test_unresolved_quest_operations_keep_activity_ownership(monkeypatch, uncertain):
    from model import activity_occupancy
    monkeypatch.setattr(activity_occupancy, 'get_expedition_run_state',
                        lambda: {'qingxi_quests': {'99': {'phase': 'held', **uncertain}}})
    monkeypatch.setattr(activity_occupancy, 'get_hunt_run_state', lambda: {})
    monkeypatch.setattr(activity_occupancy, 'get_replica_run_state', lambda: {})
    assert activity_occupancy.get_activity_block_reason(99)
    assert not activity_occupancy.get_activity_block_reason(99, include_qingxi=False)


def test_scheduler_does_not_run_without_explicit_configuration(monkeypatch):
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {})
    act = AsyncMock()
    monkeypatch.setattr(quests, 'action', act)
    asyncio.run(quests.scheduler(1))
    act.assert_not_awaited()


def test_browser_handles_regions_and_puzzles_but_leaves_fishing_to_its_plan():
    assert quests.browser_required([{'reason': 'region_travel_required'}])
    assert quests.browser_required([{'reason': 'live_node_unavailable', 'objective_type': 'puzzle'}])
    assert not quests.browser_required([{'reason': 'gameplay_required', 'objective_type': 'catch'}])
    assert not quests.browser_required([{'reason': 'reward_choice_required'}])


def test_town_fishing_redirect_requires_enabled_account_and_fresh_active_task(monkeypatch):
    from model.features.fishing_dwelling_protocol import placement
    record = {'observed_at': 100, 'observed': {'qx_q06': {'status': 'active'}}}
    config = {'enabled': True, 'participant_identity_ids': [42]}
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {'qingxi_quests': config})
    assert quests.fishing_task_site(42, 101) == 'qingxi-riverside'
    assert placement('qingxi-riverside', 'ngw')['position'] == [54, .2, 73.79]
    assert quests.fishing_task_site(43, 101) is None
    assert quests.fishing_task_site(42, 200000) is None
    record['observed']['qx_q06']['status'] = 'ready'
    assert quests.fishing_task_site(42, 101) is None
    record['observed']['qx_q06']['status'] = 'active'
    config['enabled'] = False
    assert quests.fishing_task_site(42, 101) is None


def test_optional_task_wakeup_does_not_turn_a_confirmed_fish_into_an_error(monkeypatch):
    record = {'phase': 'waiting_gameplay'}
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock(side_effect=RuntimeError('disk_write_failed')))
    assert quests.observe_town_catch(42) is False
    assert record['next_at'] > 0


def test_town_catch_wakes_only_review_when_gameplay_is_uncertain(monkeypatch):
    record = {'phase': 'held', 'gameplay_uncertain': True, 'review_next_at': 9999}
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests.time, 'time', lambda: 100)
    assert quests.observe_town_catch(42)
    assert record['review_next_at'] == 160
    assert record['phase'] == 'held' and record['gameplay_uncertain'] is True
    assert 'next_at' not in record


@pytest.mark.parametrize('taint', ['', 'wrong_player', 'unconfirmed', 'pending', 'error', 'negative_receipt'])
def test_recovered_disk_journal_requires_exact_successful_cast_evidence(tmp_path, taint):
    import json
    result = {'player': 42, 'server_checked': True, 'pending': 0, 'error': '',
              'receipts': [{'resource': 'field-cast', 'action': 'events', 'ok': True,
                            'operation_id': 'field-cast:begin:123', 'at': 11}]}
    if taint == 'wrong_player':
        result['player'] = 43
    elif taint == 'unconfirmed':
        result['server_checked'] = False
    elif taint == 'pending':
        result['pending'] = 1
    elif taint == 'error':
        result['error'] = 'unknown'
    elif taint == 'negative_receipt':
        result['receipts'][0]['ok'] = False
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'browser': {}, 'previous_browser': {'evidence_dir': str(tmp_path)}}
    assert quests.confirmed_cast_receipts(record, 42) == (
        [] if taint else [{'operation_id': 'field-cast:begin:123', 'at': 11}])


@pytest.mark.parametrize('taint', ['', 'mainline_started', 'no_fresh_receipt', 'pending', 'active_combat', 'flight_locked'])
def test_verified_entry_failure_needs_new_status_and_no_unresolved_gameplay(monkeypatch, tmp_path, taint):
    import json
    result = {'player': 42, 'entry_error': 'qingxi_enter_failed: 资源加载失败', 'server_checked': True,
              'final_status': {'fresh': True}, 'pending': 0, 'stored_pending': [], 'casts': 0, 'combat_active': False}
    if taint == 'mainline_started':
        result['mainline_started_at'] = 10
    elif taint == 'no_fresh_receipt':
        result['final_status']['fresh'] = False
    elif taint == 'pending':
        result['stored_pending'] = [{'operation_id': 'unknown'}]
    elif taint == 'active_combat':
        result['combat_active'] = True
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    if not taint:
        assert record['browser_review']['reason'] == 'verified_entry_failure'


@pytest.mark.parametrize('taint', ['', 'pending', 'missing_final', 'unknown_write', 'active_instance', 'unknown_report', 'held', 'flight_locked'])
def test_lost_final_status_recovers_only_drained_safe_stops(monkeypatch, tmp_path, taint):
    import json
    result = {'player': 42, 'phase': 'bounded_stop', 'server_checked': False, 'error': '',
              'final_status': {'fresh': False}, 'pending': 0, 'stored_pending': [],
              'overworld': True, 'combat_active': False, 'report': {'error': '主线运行达到设置的时限'}}
    if taint == 'pending':
        result['pending'] = 1
    elif taint == 'missing_final':
        result.pop('final_status')
    elif taint == 'unknown_write':
        result['error'] = 'quest_transport_unconfirmed'
    elif taint == 'active_instance':
        result.update(overworld=False, combat_active=True)
    elif taint == 'unknown_report':
        result['report']['error'] = '击杀结算未确认'
    elif taint == 'held':
        result['phase'] = 'held'
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    if not taint:
        assert record['browser_review']['reason'] == 'fresh_status_after_drained_browser_stop'


@pytest.mark.parametrize('uncertainty', [{'phase': 'held'}, {'pending': {'operationId': 'old'}},
                                      {'gameplay_uncertain': True}, {'gather': {'gatherId': 'old'}}])
def test_scheduler_periodically_reviews_uncertainty_without_mutating(monkeypatch, uncertainty):
    from model.features import cave_treasure_runtime as cave
    record = {'next_at': 0, **uncertainty}
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {
        'qingxi_quests': {'enabled': True, 'participant_identity_ids': [42]}})
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda _, **kw: not kw.get('include_qingxi') is False)
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: Mock(locked=lambda: False))
    act = AsyncMock()
    monkeypatch.setattr(quests, 'action', act)
    asyncio.run(quests.scheduler(100))
    act.assert_awaited_once_with({'action': 'refresh', 'send_as_id': 42})
    assert record['review_next_at'] == 1900
    act.reset_mock()
    asyncio.run(quests.scheduler(101))
    act.assert_not_awaited()


@pytest.mark.parametrize('taint', ['', 'wrong_player', 'new_policy', 'unknown_write', 'pending', 'disk_pending',
                                  'stale', 'active_combat', 'flight_locked', 'outside_town', 'other_region',
                                  'bad_position', 'missing_position', 'other_error'])
def test_navigation_upgrade_recovers_only_once_after_verified_safe_return(monkeypatch, tmp_path, taint):
    import json
    result = {'player': 42, 'upstream_version': '2.0.53', 'phase': 'held', 'server_checked': True,
              'final_status': {'fresh': True}, 'error': '', 'pending': 0, 'stored_pending': [],
              'combat_active': False, 'position': [0, .076, 108], 'world': {'current': 'yue'},
              'report': {'error': '找不到可步行路线：[-35,0,-410] · 先步行到可起飞的室外空地'}}
    if taint == 'wrong_player':
        result['player'] = 43
    elif taint == 'new_policy':
        result['navigation_policy'] = 1
    elif taint == 'unknown_write':
        result['error'] = 'quest_transport_unconfirmed'
    elif taint == 'pending':
        result['pending'] = 1
    elif taint == 'disk_pending':
        result['stored_pending'] = [{'operation_id': 'unknown'}]
    elif taint == 'stale':
        result['final_status']['fresh'] = False
    elif taint == 'active_combat':
        result['combat_active'] = True
    elif taint == 'outside_town':
        result['position'][2] = -379
    elif taint == 'other_region':
        result['world']['current'] = 'jiuguo'
    elif taint == 'bad_position':
        result['position'][0] = None
    elif taint == 'missing_position':
        result.pop('position')
    elif taint == 'other_error':
        result['report']['error'] = '击杀结算未确认'
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    if not taint:
        assert record['browser_review']['reason'] == 'legacy_navigation_safe_return'
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    elif taint != 'flight_locked':
        call.assert_not_awaited()


@pytest.mark.parametrize('taint', ['', 'missing_cancel', 'failed_cancel', 'new_start', 'pending',
                                  'stale_status', 'active_combat', 'flight_locked'])
def test_confirmed_movement_gather_cancel_recovers_without_replaying_it(monkeypatch, tmp_path, taint):
    import json
    result = {'player': 42, 'phase': 'held', 'server_checked': True, 'error': '',
              'final_status': {'fresh': True}, 'pending': 0, 'stored_pending': [], 'combat_active': False,
              'report': {'error': '采集中发生位移，已中断'},
              'receipts': [{'resource': 'quests', 'action': 'gather-start', 'ok': True},
                           {'resource': 'quests', 'action': 'gather-cancel', 'ok': True}]}
    if taint == 'missing_cancel':
        result['receipts'].pop()
    elif taint == 'failed_cancel':
        result['receipts'][-1]['ok'] = False
    elif taint == 'new_start':
        result['receipts'].append(result['receipts'][0])
    elif taint == 'pending':
        result['pending'] = 1
    elif taint == 'stale_status':
        result['final_status']['fresh'] = False
    elif taint == 'active_combat':
        result['combat_active'] = True
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    if not taint:
        assert record['browser_review']['reason'] == 'confirmed_gather_cancel_after_movement'
        assert call.await_args.kwargs['_api_path'].endswith('/flight')


def test_browser_guard_refuses_trial_starts_and_unverified_mutations(tmp_path):
    import shutil
    import subprocess
    from tools.qingxi_quest_browser import GUARD
    if shutil.which('node') is None:
        pytest.skip('Node is required to exercise the browser guard')
    source = """global.window=global;global.location={href:'https://asc.aiopenai.app/',origin:'https://asc.aiopenai.app'};let calls=0;
global.localStorage={getItem:()=>null,setItem:()=>{}};
global.performance={now:()=>100000*(calls+1)};
global.fetch=async(input)=>{calls++;return new Response(JSON.stringify({ok:true,account:{playerId:42}}),{status:200})};
""" + GUARD.replace('__CONFIG__', '{"player":42}') + """
(async()=>{
const url='https://asc.aiopenai.app/api/miniapp/xianxia-dwelling/';
for(const [path,body] of [['trial',{action:'start'}],['quests',{action:'accept'}]]){
 let rejected=false;try{await fetch(url+path,{body:JSON.stringify(body)})}catch{rejected=true}
 if(!rejected)throw Error('unsafe request allowed');
}
if(calls!==0)throw Error('blocked request dispatched');
await fetch(url+'start',{body:'{}'});
await fetch(url+'quests',{body:JSON.stringify({action:'accept',operationId:'one'})});
if(calls!==2||__qx.player!==42)throw Error('verified request blocked');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    script = tmp_path / 'guard.js'
    script.write_text(source)
    subprocess.run(['node', str(script)], check=True, timeout=10, capture_output=True)


def test_upstream_source_is_pinned_and_checked_before_browser_launch():
    import json
    from tools.qingxi_quest_browser import VENDOR, browser_script
    source = browser_script(42)
    provenance = json.loads((VENDOR / 'source.json').read_text())
    assert provenance['commit'] == '2d0c11fbaa9891a6e9a4f09751dc59bc23b56bb2'
    assert source.index('const config =') < source.index('==UserScript==')


def test_legacy_review_requires_proof_no_quest_mutations_were_sent(tmp_path):
    import json
    result = {'player': 42, 'pending': 0, 'requests': 2,
              'receipts': [{'action': 'status', 'ok': True}]}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(result))
    record = {'browser': {'error': 'quest_http_200_unconfirmed', 'evidence_dir': str(tmp_path)}}
    assert quests.legacy_cast_only(record, 42)
    assert not quests.legacy_cast_only(record, 43)
    result['receipts'][0]['action'] = 'field-engage'
    path.write_text(json.dumps(result))
    assert not quests.legacy_cast_only(record, 42)


def test_initial_status_transport_failure_can_be_reviewed_but_writes_cannot(tmp_path):
    import json
    result = {'player': 42, 'pending': 0, 'pending_operations': [], 'casts': 0,
              'upstream_version': '2.0.53', 'requests': 2,
              'receipts': [{'resource': 'quests', 'action': 'status', 'ok': True},
                           {'resource': 'location', 'action': 'open', 'ok': True}]}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(result))
    record = {'browser': {'error': 'quest_transport_unconfirmed', 'evidence_dir': str(tmp_path)}}
    assert quests.read_only_browser_failure(record, 42)
    result['pending_operations'] = [{'operation_id': 'unknown'}]
    path.write_text(json.dumps(result))
    assert not quests.read_only_browser_failure(record, 42)
    result['pending'] = 1
    result['pending_operations'] = [{'resource': 'location', 'action': 'save'}]
    path.write_text(json.dumps(result))
    assert quests.read_only_browser_failure(record, 42)


def test_native_entry_failure_requires_final_query_only_evidence(tmp_path):
    import json
    result = {'player': 42, 'upstream_version': '2.0.53', 'requests': 1, 'casts': 0,
              'pending': 0, 'pending_operations': [],
              'report': {'error': '游戏未接受郊外战斗入口，请确认人物已进入区域'},
              'receipts': [{'resource': 'quests', 'action': 'status', 'ok': True}]}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(result))
    record = {'browser': {'error': '', 'server_checked': True, 'evidence_dir': str(tmp_path)}}
    assert quests.read_only_browser_failure(record, 42)
    record['browser']['server_checked'] = False
    assert not quests.read_only_browser_failure(record, 42)
    record['browser']['server_checked'] = True
    result['receipts'].append({'resource': 'quests', 'action': 'field-engage', 'ok': True})
    path.write_text(json.dumps(result))
    assert not quests.read_only_browser_failure(record, 42)


@pytest.mark.parametrize('held', [False, True])
def test_reward_choice_update_wakes_waiting_task_but_never_bypasses_hold(monkeypatch, held):
    from model.features import cave_treasure_runtime as cave
    config = {'enabled': True, 'participant_identity_ids': [42], 'choices': {'qx_q21': 'release'}}
    record = {'phase': 'held' if held else 'waiting_gameplay', 'next_at': 999, 'choices_seen': {}}
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {'qingxi_quests': config})
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda _: None)
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: Mock(locked=lambda: False))
    act = AsyncMock()
    monkeypatch.setattr(quests, 'action', act)
    asyncio.run(quests.scheduler(1))
    if held:
        act.assert_not_awaited()
    else:
        act.assert_awaited_once_with({'action': 'run', 'send_as_id': 42})


@pytest.mark.parametrize('outcome', ['missing', 'malformed', 'cancelled', 'unconfirmed', 'wrong_player',
                                     'flight_locked', 'flight_missing', 'flight_unconfirmed', 'flight_timeout', 'confirmed'])
def test_browser_requires_confirmed_final_receipt_to_release_hold(monkeypatch, tmp_path, outcome):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from model.features import cave_treasure_miniapp, qingxi_trial_browser

    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data',
                        AsyncMock(return_value='https://fixture.invalid/launch'))
    record, durable = {}, []
    monkeypatch.setattr(quests, 'save', lambda: durable.append(copy.deepcopy(record)))

    async def spawn(*args, **kwargs):
        assert durable[-1]['gameplay_uncertain'] is True
        if outcome == 'cancelled':
            raise asyncio.CancelledError()
        path = Path(args[args.index('--result') + 1])
        if outcome == 'malformed':
            path.write_text('{')
        elif outcome != 'missing':
            path.write_text(json.dumps({'player': 43 if outcome == 'wrong_player' else 42,
                                        'server_checked': outcome != 'unconfirmed'}))
        return SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))

    monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', spawn)
    flight = ({'ok': True} if outcome == 'flight_missing' else {
        'ok': outcome != 'flight_unconfirmed', 'mana': {'flightLocked': outcome == 'flight_locked'}})
    verify = AsyncMock(side_effect=RuntimeError('timeout')) if outcome == 'flight_timeout' else AsyncMock(return_value=flight)
    flow = quests.run_gameplay(SimpleNamespace(identity_id=42), 'token', 'url', record, lambda: True,
                              verify_flight=verify)
    if outcome == 'confirmed':
        asyncio.run(flow)
        assert record['gameplay_uncertain'] is False
    else:
        expected = (asyncio.CancelledError if outcome == 'cancelled' else
                    FileNotFoundError if outcome == 'missing' else
                    json.JSONDecodeError if outcome == 'malformed' else RuntimeError)
        with pytest.raises(expected):
            asyncio.run(flow)
        assert record['gameplay_uncertain'] is True
    assert not list(tmp_path.glob('.runtime/**/launch.url'))


@pytest.mark.parametrize('checked,error,locked,released', [
    (True, '', False, True), (True, '', True, False), (True, '', None, False),
    (False, '', False, False), (True, 'unknown_write', False, False),
])
def test_confirmed_browser_must_wait_for_fresh_server_flight_closure(monkeypatch, checked, error, locked, released):
    record = {'phase': 'held', 'gameplay_uncertain': True, 'last_error': 'qingxi_flight_requires_review',
              'browser': {'server_checked': checked, 'error': error}}
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': locked}})
    monkeypatch.setattr(quests, 'save', Mock())
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is not released


@pytest.fixture
def flight_hold(tmp_path):
    import json
    result = {'player': 42, 'upstream_version': '2.0.53', 'casts': 0, 'requests': 2,
              'pending': 1, 'pending_operations': [{'resource': 'flight', 'action': 'tick'}],
              'receipts': [{'resource': 'quests', 'action': 'status', 'ok': True},
                           {'resource': 'flight', 'action': 'start', 'ok': True},
                           {'resource': 'flight', 'action': 'tick', 'ok': False}]}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True,
              'browser': {'error': 'quest_http_200_non_json', 'evidence_dir': str(tmp_path)}}
    return record, result, path


@pytest.mark.parametrize('taint', ['quest_write', 'takeoff', 'field_cast', 'inflight_cast', 'wrong_player', 'request_gap'])
def test_flight_review_never_clears_unconfirmed_gameplay(flight_hold, taint):
    import json
    record, result, path = flight_hold
    assert quests.native_flight_only_failure(record, 42)
    if taint == 'quest_write':
        result['receipts'].append({'resource': 'quests', 'action': 'turn-in', 'ok': True})
    elif taint == 'takeoff':
        result['pending_operations'][0]['action'] = 'start'
    elif taint == 'field_cast':
        result['casts'] = 1
    elif taint == 'inflight_cast':
        result['pending'] = 2
    elif taint == 'wrong_player':
        result['player'] = 43
    else:
        result['requests'] += 1
    path.write_text(json.dumps(result))
    assert not quests.native_flight_only_failure(record, 42)


@pytest.mark.parametrize('reply,closed', [
    ({'ok': True, 'mana': {'flightLocked': False}}, True),
    ({'ok': True, 'mana': {'flightLocked': True}}, False),
    ({'ok': True, 'mana': {'flightLocked': 0}}, False),
    ({'ok': True}, False), ({'ok': False, 'mana': {'flightLocked': False}}, False),
])
def test_flight_hold_requires_fresh_explicit_server_closure(monkeypatch, flight_hold, reply, closed):
    record, _, _ = flight_hold
    call, save = AsyncMock(return_value=reply), Mock()
    monkeypatch.setattr(quests, 'save', save)
    asyncio.run(quests.review_browser_hold(call, record, 42))
    call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    assert record['gameplay_uncertain'] is not closed
    assert (record['phase'] == 'waiting_gameplay') is closed
    assert save.call_count == int(closed)


@pytest.fixture
def orphan_cast_hold(tmp_path):
    import json
    result = {'player': 42, 'upstream_version': '2.0.53', 'casts': 1, 'requests': 1,
              'pending': 1, 'pending_operations': [{'resource': 'field-cast', 'action': 'events',
                                                  'operation_id': 'field-cast:4', 'at': 10}],
              'receipts': [{'resource': 'quests', 'action': 'status', 'ok': True},
                           {'resource': 'field-cast', 'action': 'events', 'ok': False,
                            'operation_id': 'field-cast:4'}]}
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(result))
    return {'phase': 'held', 'gameplay_uncertain': True, 'browser': {
        'error': 'quest_http_200_non_json', 'evidence_dir': str(tmp_path)}}, result, path


@pytest.mark.parametrize('taint', ['engage', 'defeat', 'turn-in', 'request_gap', 'pending_gap',
                                 'unknown_cast', 'identity', 'reward', 'settlement', 'version'])
def test_orphan_proof_review_excludes_all_gameplay_uncertainty(orphan_cast_hold, taint):
    import json
    record, result, path = orphan_cast_hold
    assert quests.orphan_cast_proof_only(record, 42)
    if taint in {'engage', 'defeat', 'turn-in'}:
        result['receipts'].append({'resource': 'quests', 'action': 'field-' + taint, 'ok': True})
    elif taint == 'request_gap':
        result['requests'] += 1
    elif taint == 'pending_gap':
        result['pending'] += 1
    elif taint == 'unknown_cast':
        result['casts'] += 1
    elif taint == 'identity':
        result['player'] = 43
    elif taint == 'reward':
        result['reward_receipts'] = [{'grants': [{'name': '灵石', 'quantity': 1}]}]
    elif taint == 'settlement':
        result['report'] = {'fieldSettlements': [{'enemyId': 'one'}]}
    else:
        result['upstream_version'] = 'other'
    path.write_text(json.dumps(result))
    assert not quests.orphan_cast_proof_only(record, 42)


@pytest.mark.parametrize('locked', [True, False, None])
def test_orphan_proof_abandonment_retains_exact_journal_and_requires_flight_closed(monkeypatch, orphan_cast_hold, locked):
    record, result, path = orphan_cast_hold
    original = path.read_bytes()
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': locked}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    assert record['gameplay_uncertain'] is (locked is not False)
    assert path.read_bytes() == original
    if locked is False:
        assert record['browser_review']['abandoned_cast_pending'] == result['pending_operations']
        assert record['browser_review']['reason'] == 'orphan_cast_no_engagement'
