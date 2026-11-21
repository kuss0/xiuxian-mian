import asyncio
import copy
import json
from pathlib import Path
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


@pytest.mark.parametrize('outcome', ['done', 'browser_failed', 'status_only'])
def test_confirmed_protocol_rewards_are_queued_before_the_next_browser(monkeypatch, outcome):
    from types import SimpleNamespace
    from model.features import cave_treasure_runtime as cave
    record, queued = {}, []
    owner = SimpleNamespace(identity_id=42, is_current=lambda: True)
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', Mock(return_value=owner))
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *a, **k: '')
    monkeypatch.setattr(quests, '_entry_url', lambda: 'offline')
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {'qingxi_quests': {'gameplay_enabled': True}})
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, 'build_cave_treasure_miniapp_adapter', Mock())
    monkeypatch.setattr(quests, 'build_pooled_miniapp_transport', Mock())
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, '_parse_public_cave_entry_url', lambda _: ('offline', 'offline', ''))
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', AsyncMock(return_value={
        'ok': True, 'player_id': 42, 'init_data': 'offline'}))
    monkeypatch.setattr(quests, 'review_browser_hold', AsyncMock())
    calls = 0
    async def protocol(*args, status_only=False, **kwargs):
        nonlocal calls
        calls += 1
        record['gaps'] = [{'reason': 'gameplay_required', 'objective_type': 'kill'}]
        if not status_only:
            record.setdefault('receipts', []).append({'operation_id': 'claim-' + str(calls), 'action': 'turn-in',
                'grants': [{'name': '灵石', 'quantity': 227 if calls == 1 else 3}]})
    async def notice(message, **kwargs):
        queued.append(message)
        return True
    async def gameplay(*args, **kwargs):
        assert len(queued) == 1 and '灵石×227' in queued[0]
        assert record['notice_attempted'] == ['claim-1']
        if outcome == 'browser_failed':
            raise RuntimeError('browser failed')
    monkeypatch.setattr(quests, 'run_protocol', protocol)
    monkeypatch.setattr(quests, 'run_gameplay', AsyncMock(side_effect=gameplay))
    monkeypatch.setattr(quests, 'send_audit_log', notice)
    ok, _ = asyncio.run(quests.run_once(42, status_only=outcome == 'status_only'))
    assert ok is (outcome != 'browser_failed')
    assert len(queued) == {'done': 2, 'browser_failed': 1, 'status_only': 0}[outcome]
    if outcome == 'done':
        assert '灵石×3' in queued[1] and '灵石×227' not in queued[1]


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
@pytest.mark.parametrize('navigation_policy', [0, 3])
def test_navigation_upgrade_recovers_only_once_after_verified_safe_return(monkeypatch, tmp_path, taint,
                                                                         navigation_policy):
    import json
    result = {'player': 42, 'upstream_version': '2.0.53', 'phase': 'held', 'server_checked': True,
              'final_status': {'fresh': True}, 'error': '', 'pending': 0, 'stored_pending': [],
              'combat_active': False, 'position': [0, .076, 108], 'world': {'current': 'yue'},
              'report': {'error': '找不到可步行路线：[-35,0,-410] · 先步行到可起飞的室外空地'},
              'navigation_policy': navigation_policy,
              'navigation_failure': {'obstaclesReviewed': True, 'recovery': {'needsTakeoffWalk': True}}}
    if taint == 'wrong_player':
        result['player'] = 43
    elif taint == 'new_policy':
        result['navigation_policy'] = 4 if navigation_policy == 3 else 1
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
        assert record['browser_review']['reason'] == (
            'native_takeoff_navigation_upgrade' if navigation_policy == 3 else 'legacy_navigation_safe_return')
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    elif taint != 'flight_locked':
        call.assert_not_awaited()


@pytest.mark.parametrize('taint', ['', 'wrong_player', 'wrong_version', 'new_policy', 'wrong_phase', 'unknown_write',
                                  'status_error', 'pending', 'disk_pending', 'active_combat', 'flight_locked',
                                  'outside_town', 'other_region', 'bad_position', 'missing_position', 'other_error',
                                  'no_settlements', 'failed_settlement', 'malformed_settlement', 'stale_observed',
                                  'future_observed', 'before_finish', 'no_observed', 'no_final', 'no_status',
                                  'failed_status', 'stale_status', 'future_status', 'unknown_receipt'])
def test_combat_navigation_upgrade_requires_drained_run_and_fresh_owner_review(monkeypatch, tmp_path, taint):
    import json
    result = {'player': 42, 'upstream_version': '2.0.53', 'navigation_policy': 1, 'phase': 'held',
              'server_checked': False, 'final_status': {'fresh': False, 'at': 100}, 'error': '',
              'pending': 0, 'stored_pending': [], 'combat_active': False, 'position': [0, .076, 108],
              'world': {'current': 'yue'}, 'report': {'error': '寻路超时：[-40.32,0,-348.76]',
                                                   'fieldSettlements': [{'ok': True}]},
              'receipts': [{'resource': 'quests', 'action': 'status', 'ok': True, 'at': 99.9}]}
    record = {'phase': 'held', 'gameplay_uncertain': True, 'observed_at': 110,
              'browser': {'evidence_dir': str(tmp_path)}}
    if taint == 'wrong_player':
        result['player'] = 43
    elif taint == 'wrong_version':
        result['upstream_version'] = '2.0.54'
    elif taint == 'new_policy':
        result['navigation_policy'] = 2
    elif taint == 'wrong_phase':
        result['phase'] = 'running'
    elif taint == 'unknown_write':
        result['error'] = 'quest_transport_unconfirmed'
    elif taint == 'status_error':
        result['status_error'] = 'quest_status_unconfirmed'
    elif taint == 'pending':
        result['pending'] = 1
    elif taint == 'disk_pending':
        result['stored_pending'] = [{'operation_id': 'unknown'}]
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
    elif taint == 'no_settlements':
        result['report']['fieldSettlements'] = []
    elif taint == 'failed_settlement':
        result['report']['fieldSettlements'][0]['ok'] = False
    elif taint == 'malformed_settlement':
        result['report']['fieldSettlements'][0] = None
    elif taint == 'stale_observed':
        record['observed_at'] = 60
    elif taint == 'future_observed':
        record['observed_at'] = 121
    elif taint == 'before_finish':
        record['observed_at'] = 99
    elif taint == 'no_observed':
        record.pop('observed_at')
    elif taint == 'no_final':
        result.pop('final_status')
    elif taint == 'no_status':
        result['receipts'] = []
    elif taint == 'failed_status':
        result['receipts'][0]['ok'] = False
    elif taint == 'stale_status':
        result['receipts'][0]['at'] = 98
    elif taint == 'future_status':
        result['receipts'][0]['at'] = 101
    elif taint == 'unknown_receipt':
        result['receipts'].append({'resource': 'quests', 'action': 'turn-in', 'ok': False})
    (tmp_path / 'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(quests.time, 'time', lambda: 120)
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    if not taint:
        assert record['browser_review']['reason'] == 'combat_interrupted_navigation_upgrade'
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    elif taint != 'flight_locked':
        call.assert_not_awaited()


@pytest.mark.parametrize('taint', ['', 'wrong_player', 'wrong_version', 'new_policy', 'running', 'other_error',
                                  'status_error', 'active_combat', 'outside_town', 'stale_status', 'flight_locked',
                                  'unmatched_id', 'other_enemy', 'old_ack', 'unknown_status', 'other_action',
                                  'extra_write', 'disk_mismatch', 'unknown_cast'])
def test_rejected_release_requires_exact_negative_receipt_and_new_status(monkeypatch, tmp_path, taint):
    import json
    pending = {'resource': 'quests', 'action': 'field-release', 'operation_id': 'release-one',
               'enemy_id': 'lantern:084', 'at': 100}
    ack = {**pending, 'at': 105, 'ok': False, 'http_status': 409, 'error': 'quest_too_far'}
    result = {'player': 42, 'upstream_version': '2.0.53', 'navigation_policy': 2, 'phase': 'held',
              'error': 'quest_http_409_quest_too_far', 'pending': 1, 'pending_operations': [pending],
              'stored_pending': [pending.copy()], 'combat_active': False, 'world': {'current': 'yue'},
              'position': [-1.05, .8375, -.8], 'receipts': [ack]}
    record = {'phase': 'held', 'gameplay_uncertain': True, 'observed_at': 110,
              'browser': {'evidence_dir': str(tmp_path)}}
    if taint == 'wrong_player':
        result['player'] = 43
    elif taint == 'wrong_version':
        result['upstream_version'] = '2.0.54'
    elif taint == 'new_policy':
        result['navigation_policy'] = 3
    elif taint == 'running':
        result['phase'] = 'running'
    elif taint == 'other_error':
        result['error'] = 'quest_transport_unconfirmed'
    elif taint == 'status_error':
        result['status_error'] = 'quest_status_unavailable'
    elif taint == 'active_combat':
        result['combat_active'] = True
    elif taint == 'outside_town':
        result['position'][2] = -374
    elif taint == 'stale_status':
        record['observed_at'] = 60
    elif taint == 'unmatched_id':
        ack['operation_id'] = 'other'
    elif taint == 'other_enemy':
        ack['enemy_id'] = 'lantern:085'
    elif taint == 'old_ack':
        ack['at'] = 99
    elif taint == 'unknown_status':
        ack['http_status'] = 503
    elif taint == 'other_action':
        pending['action'] = ack['action'] = result['stored_pending'][0]['action'] = 'turn-in'
    elif taint == 'extra_write':
        result['pending'] = 2
    elif taint == 'disk_mismatch':
        result['stored_pending'] = []
    elif taint == 'unknown_cast':
        result['receipts'].append({'resource': 'field-cast', 'ok': False})
    (tmp_path / 'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(quests.time, 'time', lambda: 120)
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    if not taint:
        assert record['browser_review']['reason'] == 'confirmed_field_release_rejection'
        assert record['browser_review']['rejected_release_pending'] == [pending]
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
for(const action of ['start','act','finish','claim','retry']){
 let rejected=false;try{await fetch(url+'trial',{body:JSON.stringify({action,playerId:42})})}catch(error){
  rejected=error.message==='quest_runner_trial_forbidden';
 }
 if(!rejected||calls!==2)throw Error('trial mutation passed the verified player guard');
}
await fetch(url+'trial',{body:JSON.stringify({action:'status',playerId:42})});
if(calls!==3)throw Error('read-only trial status blocked');
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
    assert provenance['commit'] == 'abbb2c54ef4e67ba78fce65918a5e98eba8e397b'
    assert provenance['version'] == '2.0.78'
    assert "(state.story?.flightLastFailure || recovery.reason)" in source
    assert 'window.__qx.navigationFailure' in source
    assert "if (!config.rewardBatch) stop('quest_runner_trial_forbidden')" in source
    assert '"rewardBatch": false' in source
    assert source.index('const config =') < source.index('==UserScript==')


@pytest.mark.parametrize('version', ['2.0.53', '2.0.78', '2.0.79'])
@pytest.mark.parametrize('taint', ['', 'identity', 'unknown_defeat', 'flight_locked'])
def test_versioned_flight_review_requires_unchanged_write_and_closure_evidence(
        monkeypatch, tmp_path, version, taint):
    import json
    result = {
        'player': 42, 'upstream_version': version, 'phase': 'held',
        'error': 'quest_transport_unconfirmed', 'casts': 0, 'requests': 2,
        'pending': 1, 'pending_operations': [{'resource': 'flight', 'action': 'stop'}],
        'stored_pending': [{'resource': 'flight', 'action': 'stop'}],
        'receipts': [{'resource': 'quests', 'action': 'status', 'ok': True},
                     {'resource': 'flight', 'action': 'stop', 'ok': True}],
    }
    if taint == 'identity':
        result['player'] = 43
    elif taint == 'unknown_defeat':
        result['pending_operations'].append({'resource': 'quests', 'action': 'field-defeat'})
        result['pending'] = 2
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True,
              'browser': {'error': result['error'], 'evidence_dir': str(tmp_path)}}
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    monkeypatch.setattr(quests, 'save', Mock())
    asyncio.run(quests.review_browser_hold(call, record, 42))
    closed = version in {'2.0.53', '2.0.78'} and not taint
    assert record['gameplay_uncertain'] is not closed
    if closed:
        assert record['browser_review']['reason'] == 'native_flight_session_closed'
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    elif taint != 'flight_locked' or version == '2.0.79':
        call.assert_not_awaited()


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
    monkeypatch.setattr(quests, '_drain_browser_process', AsyncMock())

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
def ready_npc_stop_result():
    return dict(player=42, upstream_version='2.0.53', phase='waiting', pending=0, pending_operations=[],
                stored_pending=[], combat_active=False, world={'current': 'yue'}, position=[0, .076, 108],
                states={'qx_q12': {'status': 'ready'}},
                report={'error': '找不到可步行路线：[57,1,5] · 可御空'},
                navigation_failure={'goal': [57, 1, 5]},
                stop_reason={'reason': 'quest_request_expired_before_send'})


@pytest.mark.parametrize('taint', ['', 'identity', 'running', 'pending', 'pending_gap', 'stored', 'combat',
                                 'world', 'position', 'goal', 'height', 'quest_active', 'wrong_receiver',
                                 'query_error', 'cast_error', 'other_error', 'cleanup', 'shutdown',
                                 'other_stop', 'stale_observation', 'flight_locked'])
def test_ready_npc_navigation_returns_to_existing_protocol_only_after_safe_review(
        monkeypatch, tmp_path, ready_npc_stop_result, taint):
    import json
    result = ready_npc_stop_result
    observed = {'qx_q12': {'status': 'ready'}}
    flight = {'ok': True, 'mana': {'flightLocked': False}}
    if taint == 'identity':
        result['player'] = 43
    elif taint == 'running':
        result['phase'] = 'running'
    elif taint == 'pending':
        result['pending'] = 1
    elif taint == 'pending_gap':
        result['pending_operations'] = [{'action': 'field-defeat'}]
    elif taint == 'stored':
        result['stored_pending'] = [{'action': 'turn-in'}]
    elif taint == 'combat':
        result['combat_active'] = True
    elif taint == 'world':
        result['world']['current'] = 'jiuguo'
    elif taint == 'position':
        result['position'] = [-40, 0, -400]
    elif taint == 'goal':
        result['navigation_failure']['goal'][0] += 10
    elif taint == 'height':
        result['navigation_failure']['goal'][1] = 30
    elif taint == 'quest_active':
        result['states']['qx_q12']['status'] = 'active'
    elif taint == 'wrong_receiver':
        result['states'] = {'qx_q13': {'status': 'ready'}}
    elif taint == 'query_error':
        result['status_error'] = 'quest_status_unavailable'
    elif taint == 'cast_error':
        result['cast_error'] = 'quest_cast_unacknowledged'
    elif taint == 'other_error':
        result['report']['error'] = '击杀结算未确认'
    elif taint == 'cleanup':
        result['cleanup_error'] = 'TimeoutError'
    elif taint == 'shutdown':
        result['browser_shutdown_error'] = 'TimeoutError'
    elif taint == 'other_stop':
        result['stop_reason']['reason'] = 'unknown'
    elif taint == 'stale_observation':
        observed['qx_q12']['status'] = 'active'
    elif taint == 'flight_locked':
        flight['mana']['flightLocked'] = True
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'observed': observed,
              'browser': {'evidence_dir': str(tmp_path)}}
    call, save = AsyncMock(return_value=flight), Mock()
    monkeypatch.setattr(quests, 'save', save)
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    assert save.call_count == int(not taint)
    if not taint:
        assert record['browser_review']['reason'] == 'verified_npc_delivery_handoff'
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')


@pytest.mark.parametrize('failure', ['', 'unknown_defeat', 'flight_locked', 'low_health', 'low_health_stale'])
def test_browser_exit_hands_ready_npc_to_protocol_without_relabeling_failure(
        monkeypatch, tmp_path, ready_npc_stop_result, failure):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from model.features import cave_treasure_miniapp, qingxi_trial_browser
    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    result = ready_npc_stop_result
    if failure.startswith('low_health'):
        result.update(phase='held', hp=1656.48, server_checked=failure == 'low_health',
                      final_status={'fresh': failure == 'low_health'},
                      report={'error': '野外气血低于 30% 且无法立即回血，已中止任务并尝试安全回城'})
    if failure == 'unknown_defeat':
        result.update(pending=1, pending_operations=[{'action': 'field-defeat'}])
    process = SimpleNamespace(returncode=1, wait=AsyncMock(return_value=1))
    async def launch(*command, **kwargs):
        Path(command[command.index('--result') + 1]).write_text(json.dumps(result))
        return process
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data',
                        AsyncMock(return_value='offline-entry'))
    monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', launch)
    monkeypatch.setattr(quests, '_drain_browser_process', AsyncMock())
    monkeypatch.setattr(quests, 'save', Mock())
    flight = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': failure == 'flight_locked'}})
    record = {}
    operation = quests.run_gameplay(SimpleNamespace(identity_id=42), 'offline', 'offline', record,
                                   lambda: True, verify_flight=flight)
    if failure and failure != 'low_health':
        with pytest.raises(RuntimeError, match='qingxi_browser_requires_review' if failure in {'unknown_defeat', 'low_health_stale'}
                           else 'qingxi_flight_requires_review'):
            asyncio.run(operation)
        assert record['gameplay_uncertain'] is True
    else:
        asyncio.run(operation)
        assert record['gameplay_uncertain'] is False
        assert record['browser']['flight_checked'] is True
        assert record['browser']['phase'] == result['phase']
        assert record['browser']['report']['error'] == result['report']['error']
        flight.assert_awaited_once()


@pytest.fixture
def native_low_health_return_result():
    import json
    from pathlib import Path

    # Field subset of the 2026-11-21 native 2.0.78 receipt, with a test identity.
    return json.loads(Path(__file__).with_name('fixtures').joinpath(
        'qingxi-native-low-health-return-20261121.json').read_text())


@pytest.mark.parametrize('taint', ['', 'stale_status', 'flight_locked', 'pending', 'unknown_error'])
def test_native_low_health_exit_requires_fresh_status_and_closed_flight(
        monkeypatch, tmp_path, native_low_health_return_result, taint):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from model.features import cave_treasure_miniapp, qingxi_trial_browser

    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    result = native_low_health_return_result
    if taint == 'stale_status':
        result['final_status']['fresh'] = False
    elif taint == 'pending':
        result['stored_pending'] = [{'operation_id': 'unknown'}]
    elif taint == 'unknown_error':
        result['report']['error'] += '；击杀结算未确认'
    process = SimpleNamespace(returncode=1, wait=AsyncMock(return_value=1))
    async def launch(*command, **kwargs):
        Path(command[command.index('--result') + 1]).write_text(json.dumps(result))
        return process
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data',
                        AsyncMock(return_value='offline-entry'))
    monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', launch)
    monkeypatch.setattr(quests, '_drain_browser_process', AsyncMock())
    monkeypatch.setattr(quests, 'save', Mock())
    flight = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    record = {}
    operation = quests.run_gameplay(SimpleNamespace(identity_id=42), 'offline', 'offline', record,
                                   lambda: True, verify_flight=flight)
    if taint:
        reason = 'qingxi_flight_requires_review' if taint == 'flight_locked' else 'qingxi_browser_requires_review'
        with pytest.raises(RuntimeError, match=reason):
            asyncio.run(operation)
    else:
        asyncio.run(operation)
        assert record['browser']['phase'] == 'waiting'
        assert record['browser']['flight_checked'] is True
    assert record['gameplay_uncertain'] is bool(taint)
    assert flight.await_count == int(taint in {'', 'flight_locked'})


def test_native_low_health_hold_recovers_on_a_new_status_read(
        monkeypatch, tmp_path, native_low_health_return_result):
    import json

    result = native_low_health_return_result
    result.update(phase='held', server_checked=False)
    result['final_status']['fresh'] = False
    ended = result['final_status']['at']
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'observed_at': ended - 1,
              'browser': {'evidence_dir': str(tmp_path)}}
    flight = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': False}})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests.time, 'time', lambda: ended + 2)
    asyncio.run(quests.review_browser_hold(flight, record, 42))
    flight.assert_not_awaited()
    assert record['gameplay_uncertain'] is True

    record['observed_at'] = ended + 1
    asyncio.run(quests.review_browser_hold(flight, record, 42))
    flight.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    assert record['gameplay_uncertain'] is False
    assert record['browser_review']['reason'] == 'verified_low_health_safe_return'


def test_mainline_parent_leaves_time_for_native_return_recovery_and_reentry(monkeypatch, tmp_path):
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from model.features import cave_treasure_miniapp, qingxi_trial_browser

    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    result_path = None
    process = SimpleNamespace(returncode=None)
    async def finish():
        # Allow the watchdog to check the deadline before the worker completes.
        await asyncio.sleep(0)
        if process.returncode is None:
            result_path.write_text(json.dumps({
                'player': 42, 'phase': 'done', 'server_checked': True,
                'health_recovery': {'phase': 'ready'}, 'final_status': {'fresh': True},
            }))
            process.returncode = 0
        return process.returncode
    process.wait = finish
    async def launch(*command, **kwargs):
        nonlocal result_path
        result_path = Path(command[command.index('--result') + 1])
        return process
    async def stop(*args):
        if process.returncode is None:
            process.returncode = -15
    elapsed = iter((0, 4210))
    monkeypatch.setattr(quests, 'time', SimpleNamespace(time=lambda: 12345,
                                                       monotonic=lambda: next(elapsed, 4210)))
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data',
                        AsyncMock(return_value='offline-entry'))
    monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', launch)
    monkeypatch.setattr(quests, '_drain_browser_process', stop)
    monkeypatch.setattr(quests, 'save', Mock())
    result = {}
    flight = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': False}})
    asyncio.run(quests.run_gameplay(SimpleNamespace(identity_id=42), 'offline', 'offline', result,
                                    lambda: True, verify_flight=flight))
    assert process.returncode == 0
    assert result['browser']['health_recovery'] == {'phase': 'ready'}
    assert result['gameplay_uncertain'] is False


@pytest.mark.parametrize('taint', ['', 'identity', 'running', 'pending', 'pending_gap', 'stored', 'combat',
                                 'world', 'position', 'nan_position', 'dead', 'nan_hp', 'unknown_error',
                                 'query_error', 'cast_error', 'entry_error', 'snapshot_error', 'cleanup_error',
                                 'browser_shutdown_error', 'browser_close_error', 'unknown_stop',
                                 'unknown_report', 'missing_final', 'bad_final', 'stale_status',
                                 'status_before_stop', 'flight_locked', 'flight_error'])
def test_low_health_safe_return_recovers_only_after_fresh_status_and_flight_proof(
        monkeypatch, tmp_path, ready_npc_stop_result, taint):
    import json
    result = ready_npc_stop_result
    result.update(phase='held', server_checked=False, hp=1656.48, final_status={'fresh': False, 'at': 100},
                  report={'error': '野外气血低于 30% 且无法立即回血，已中止任务并尝试安全回城'})
    observed_at = 101
    if taint == 'identity':
        result['player'] = 43
    elif taint == 'running':
        result['phase'] = 'running'
    elif taint == 'pending':
        result['pending'] = 1
    elif taint == 'pending_gap':
        result['pending_operations'] = [{'action': 'field-defeat'}]
    elif taint == 'stored':
        result['stored_pending'] = [{'action': 'turn-in'}]
    elif taint == 'combat':
        result['combat_active'] = True
    elif taint == 'world':
        result['world']['current'] = 'jiuguo'
    elif taint == 'position':
        result['position'] = [-39, 0, -492]
    elif taint == 'nan_position':
        result['position'][0] = float('nan')
    elif taint in {'dead', 'nan_hp'}:
        result['hp'] = 0 if taint == 'dead' else float('nan')
    elif taint == 'unknown_error':
        result['error'] = 'quest_transport_unconfirmed'
    elif taint in {'query_error', 'cast_error', 'entry_error', 'snapshot_error', 'cleanup_error',
                   'browser_shutdown_error', 'browser_close_error'}:
        result['status_error' if taint == 'query_error' else taint] = 'unconfirmed'
    elif taint == 'unknown_stop':
        result['stop_reason']['reason'] = 'unknown'
    elif taint == 'unknown_report':
        result['report']['error'] = '击杀结算未确认'
    elif taint == 'missing_final':
        result.pop('final_status')
    elif taint == 'bad_final':
        result['final_status'] = 'unknown'
    elif taint == 'stale_status':
        observed_at = 0
    elif taint == 'status_before_stop':
        observed_at = 99
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'observed_at': observed_at,
              'browser': {'evidence_dir': str(tmp_path)}}
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    if taint == 'flight_error':
        call.side_effect = RuntimeError('flight unavailable')
    monkeypatch.setattr(quests.time, 'time', lambda: 102)
    save = Mock()
    monkeypatch.setattr(quests, 'save', save)
    if taint == 'flight_error':
        with pytest.raises(RuntimeError, match='flight unavailable'):
            asyncio.run(quests.review_browser_hold(call, record, 42))
    else:
        asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    assert save.call_count == int(not taint)
    if not taint:
        assert record['phase'] == 'waiting_gameplay'
        assert record['browser_review']['reason'] == 'verified_low_health_safe_return'
        assert json.loads((tmp_path / 'result.json').read_text()) == result
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')


@pytest.mark.parametrize('taint', ['', 'new_transport', 'identity', 'running', 'shutdown', 'cast_count',
                                 'pending_count', 'stored_changed', 'request_gap', 'settlement', 'reward',
                                 'defeat', 'turn-in', 'cast_done', 'receipt_missing', 'receipt_success',
                                 'receipt_wrong_enemy', 'receipt_stale', 'receipt_other_error',
                                 'wrong_action', 'fresh_failure', 'fresh_wrong_identity', 'fresh_missing_enemy',
                                 'fresh_corpses', 'flight_locked'])
def test_old_unconfirmed_preparation_upgrade_excludes_kills_and_rewards(monkeypatch, tmp_path, taint):
    import copy
    import json
    pending = [dict(resource='quests', action='field-engage', operation_id='engage', enemy_id='lantern:083', at=10),
               dict(resource='field-cast', action='events', operation_id='cast', at=10, attempts=1,
                    events=[dict(k='use', s=100, key='pierce', pos=[0, 0])])]
    receipts = [dict(resource='quests', action='status', ok=True)] + [
        {k: v for k, v in row.items() if k not in {'events', 'attempts'}} | dict(
            ok=False, http_status=200, error='non_json', parse_error='AbortError', content_type='application/json', at=11)
        for row in pending]
    result = dict(player=42, upstream_version='2.0.53', navigation_policy=4, phase='held',
                  error='quest_http_200_non_json', casts=1, requests=2, pending=4,
                  pending_operations=pending, stored_pending=copy.deepcopy(pending), receipts=receipts,
                  report={'fieldSettlements': []}, reward_receipts=[])
    fresh = {'ok': True, 'quests': {'states': {}, 'corpses': [], 'field': {
        'enemies': [{'id': 'lantern:083', 'generation': 5, 'hp': 13869}]}}}
    flight = {'ok': True, 'mana': {'flightLocked': False}}
    if taint == 'new_transport':
        result['transport_policy'] = 1  # without a verified previous run
    elif taint == 'identity':
        result['player'] = 43
    elif taint == 'running':
        result['phase'] = 'running'
    elif taint == 'shutdown':
        result['browser_shutdown_error'] = 'TimeoutError'
    elif taint in {'cast_count', 'pending_count', 'request_gap'}:
        result[{'cast_count': 'casts', 'pending_count': 'pending', 'request_gap': 'requests'}[taint]] += 1
    elif taint == 'stored_changed':
        result['stored_pending'][0]['at'] += 1
    elif taint == 'settlement':
        result['report']['fieldSettlements'].append({'ok': False})
    elif taint == 'reward':
        receipts[1]['grants'] = [{'name': '灵石', 'quantity': 1}]
    elif taint in {'defeat', 'turn-in'}:
        receipts.append(dict(resource='quests', action='field-defeat' if taint == 'defeat' else taint, ok=False))
    elif taint == 'cast_done':
        pending[1]['events'][0]['k'] = 'done'
        result['stored_pending'] = copy.deepcopy(pending)
    elif taint == 'receipt_missing':
        receipts.pop()
    elif taint == 'receipt_success':
        receipts[1]['ok'] = True
    elif taint == 'receipt_wrong_enemy':
        receipts[1]['enemy_id'] = 'other'
    elif taint == 'receipt_stale':
        receipts[1]['at'] = 9
    elif taint == 'receipt_other_error':
        receipts[1]['error'] = 'unknown'
    elif taint == 'wrong_action':
        pending[0]['action'] = 'field-defeat'
        result['stored_pending'] = copy.deepcopy(pending)
    elif taint == 'fresh_failure':
        fresh['ok'] = False
    elif taint == 'fresh_wrong_identity':
        fresh['playerId'] = 43
    elif taint == 'fresh_missing_enemy':
        fresh['quests']['field']['enemies'] = []
    elif taint == 'fresh_corpses':
        fresh['quests']['corpses'] = [{'id': 'unknown'}]
    elif taint == 'flight_locked':
        flight['mana']['flightLocked'] = True
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    call, save = AsyncMock(side_effect=[fresh, flight]), Mock()
    monkeypatch.setattr(quests, 'save', save)
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    assert (record['phase'] == 'waiting_gameplay') is (not taint)
    assert save.call_count == int(not taint)
    if not taint:
        assert call.await_count == 2
        assert call.await_args_list[0].args == ('status',)
        assert call.await_args_list[1].kwargs == {'_api_path': '/api/miniapp/xianxia-dwelling/flight'}
        assert record['browser_review']['reason'] == 'unconfirmed_field_preparation_upgrade'
        assert record['browser_review']['abandoned_preparation_pending'] == pending


@pytest.mark.parametrize('taint', ['', 'no_request', 'partial_ids', 'extra_id', 'stale_request', 'other_run',
                                 'identity', 'running', 'stored_changed', 'settlement', 'reward', 'loot',
                                 'defeat_pending', 'defeat_receipt', 'turn-in', 'fresh_failure',
                                 'fresh_wrong_identity', 'fresh_missing_enemy', 'fresh_dead_enemy',
                                 'fresh_corpses', 'flight_locked'])
def test_operator_release_needs_exact_operations_and_fresh_server_facts(monkeypatch, tmp_path, taint):
    """Shapes from the two held runs of 2026-11-19/20 (field-release 409 body abort; engage+cast body abort)."""
    import copy
    import json
    pending = [dict(resource='quests', action='field-release', operation_id='release', enemy_id='lantern:090', at=10),
               dict(resource='quests', action='field-engage', operation_id='engage', enemy_id='longwind:032', at=11),
               dict(resource='field-cast', action='events', operation_id='cast', at=11, attempts=1,
                    events=[dict(k='use', s=100, key='pierce', pos=[0, 0])])]
    receipts = [dict(resource='quests', action='status', ok=True),
                dict(resource='flight', action='status', ok=False, error='request_aborted'),
                dict(resource='quests', action='field-release', ok=False, http_status=409, error='non_json',
                     parse_error='AbortError', operation_id='release', enemy_id='lantern:090', at=12)]
    result = dict(player=42, upstream_version='2.0.78', navigation_policy=4, transport_policy=1, phase='held',
                  error='quest_http_409_non_json', casts=3, requests=7, pending=5, combat_active=True,
                  pending_operations=pending, stored_pending=copy.deepcopy(pending), receipts=receipts,
                  report={'fieldSettlements': []}, reward_receipts=[])
    request = {'operation_ids': ['cast', 'engage', 'release'], 'note': 'user approved', 'at': 100,
               'evidence_dir': str(tmp_path)}
    fresh = {'ok': True, 'quests': {'states': {}, 'corpses': [], 'field': {'enemies': [
        {'id': 'lantern:090', 'generation': 1, 'hp': 196887}, {'id': 'longwind:032', 'generation': 2, 'hp': 18116}]}}}
    flight = {'ok': True, 'mana': {'flightLocked': False}}
    if taint == 'no_request':
        request = None
    elif taint == 'partial_ids':
        request['operation_ids'].pop()
    elif taint == 'extra_id':
        request['operation_ids'].append('other')
    elif taint == 'stale_request':
        request['at'] = -900
    elif taint == 'other_run':
        request['evidence_dir'] = str(tmp_path / 'older')
    elif taint == 'identity':
        result['player'] = 43
    elif taint == 'running':
        result['phase'] = 'running'
    elif taint == 'stored_changed':
        result['stored_pending'][0]['at'] += 1
    elif taint == 'settlement':
        result['report']['fieldSettlements'].append({'ok': False})
    elif taint == 'reward':
        receipts[2]['grants'] = [{'name': '灵石', 'quantity': 1}]
    elif taint == 'loot':
        receipts[2]['pickedItems'] = [{'itemId': 'x', 'quantity': 1}]
    elif taint == 'defeat_pending':
        pending[1]['action'] = 'field-defeat'
        result['stored_pending'] = copy.deepcopy(pending)
    elif taint in {'defeat_receipt', 'turn-in'}:
        receipts.append(dict(resource='quests', action='field-defeat' if taint == 'defeat_receipt' else taint, ok=False))
    elif taint == 'fresh_failure':
        fresh['ok'] = False
    elif taint == 'fresh_wrong_identity':
        fresh['playerId'] = 43
    elif taint == 'fresh_missing_enemy':
        fresh['quests']['field']['enemies'].pop()
    elif taint == 'fresh_dead_enemy':
        fresh['quests']['field']['enemies'][0]['hp'] = 0
    elif taint == 'fresh_corpses':
        fresh['quests']['corpses'] = [{'id': 'unknown'}]
    elif taint == 'flight_locked':
        flight['mana']['flightLocked'] = True
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    if request:
        record['operator_release'] = request
    call, save = AsyncMock(side_effect=[fresh, flight]), Mock()
    monkeypatch.setattr(quests, 'save', save)
    monkeypatch.setattr(quests.time, 'time', lambda: 160)
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    assert save.call_count == int(not taint)
    if not taint:
        review = record['browser_review']
        assert review['reason'] == 'operator_reviewed_field_pending'
        assert review['rejected_release_pending'] == pending[:1]
        assert review['abandoned_preparation_pending'] == pending[1:]
        assert review['operator']['note'] == 'user approved' and 'operator_release' not in record
        assert call.await_args_list[0].args == ('status',)
        assert call.await_args_list[1].kwargs == {'_api_path': '/api/miniapp/xianxia-dwelling/flight'}


def test_operator_release_action_validates_and_only_schedules_a_refresh(monkeypatch):
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': '/evidence/run'}}
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, '_TASKS', {})
    seen = []

    async def run_once(identity_id, **kwargs):
        seen.append((identity_id, kwargs))
    monkeypatch.setattr(quests, 'run_once', run_once)
    monkeypatch.setattr(quests, 'track_background_task', lambda task: task)

    async def scenario():
        for bad in ({'operation_ids': []}, {'operation_ids': ['a']}, {'operation_ids': 'a', 'note': 'x'},
                    {'operation_ids': ['a'], 'note': ' '}):
            ok, _, _ = await quests.action({'action': 'operator_release', 'send_as_id': 42, **bad})
            assert not ok and 'operator_release' not in record
        ok, _, _ = await quests.action({'action': 'operator_release', 'send_as_id': 43,
                                        'operation_ids': ['a'], 'note': 'x'})
        assert not ok
        ok, _, _ = await quests.action({'action': 'operator_release', 'send_as_id': 42,
                                        'operation_ids': ['a'], 'note': ' approved '})
        assert ok
        await asyncio.gather(*list(quests._TASKS.values()))
    asyncio.run(scenario())
    assert record['operator_release']['operation_ids'] == ['a'] and record['operator_release']['note'] == 'approved'
    assert record['operator_release']['evidence_dir'] == '/evidence/run'
    assert seen == [(42, {'status_only': True, 'reward_batch': False})]


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


def _held_record(tmp_path, result, **previous):
    import json
    (tmp_path / 'result.json').write_text(json.dumps(result))
    base = {'server_checked': True, 'error': '', 'pending': 0, 'flight_checked': True}
    return {'phase': 'held', 'gameplay_uncertain': True, 'observed_at': __import__('time').time(),
            'browser': {'evidence_dir': str(tmp_path)}, 'previous_browser': {**base, **previous}}


@pytest.mark.parametrize('previous', [{}, {'server_checked': False}, {'error': 'quest_http_200_non_json'},
                                      {'pending': 1}, {'flight_checked': False}])
def test_new_transport_preparation_needs_a_verified_previous_run(monkeypatch, tmp_path, previous):
    """Real 2026-11-17 16:22 case: engage and one 'use' upload both ended in AbortError."""
    pending = [dict(resource='quests', action='field-engage', operation_id='engage', enemy_id='lantern:083', at=10),
               dict(resource='field-cast', action='events', operation_id='cast', at=10, attempts=1,
                    events=[dict(k='use', s=100, key='pierce', pos=[0, 0])])]
    receipts = [dict(resource='quests', action='status', ok=True),
                dict(resource='flight', action='start', ok=True)] + [
        {k: v for k, v in row.items() if k not in {'events', 'attempts'}} | dict(
            ok=False, http_status=200, error='non_json', parse_error='AbortError', content_type='application/json', at=11)
        for row in pending]
    result = dict(player=42, upstream_version='2.0.53', navigation_policy=4, transport_policy=1, phase='held',
                  error='quest_http_200_non_json', casts=1, requests=2, pending=4,
                  pending_operations=pending, stored_pending=pending, receipts=receipts,
                  report={'fieldSettlements': []}, reward_receipts=[])
    record = _held_record(tmp_path, result, **previous)
    fresh = {'ok': True, 'quests': {'states': {}, 'corpses': [], 'field': {
        'enemies': [{'id': 'lantern:083', 'generation': 5, 'hp': 13869}]}}}
    call = AsyncMock(side_effect=[fresh, {'ok': True, 'mana': {'flightLocked': False}}])
    monkeypatch.setattr(quests, 'save', Mock())
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(previous)
    if not previous:
        assert record['browser_review']['reason'] == 'unconfirmed_field_preparation_upgrade'
        assert record['browser_review']['abandoned_preparation_pending'] == pending


@pytest.mark.parametrize('taint', [
    '', 'identity', 'old_transport', 'previous_unverified', 'http_500', 'json_body', 'use_event', 'two_pending',
    'stored_changed', 'begin_missing', 'begin_failed', 'later_success', 'other_failed_write', 'reward',
    'failed_settlement', 'shutdown', 'stale_status', 'flight_locked'])
def test_gateway_refused_cast_completion_is_abandoned_after_fresh_checks(monkeypatch, tmp_path, taint):
    """Real 2026-11-17 22:41 case: three kills acknowledged, then Cloudflare 521 on the last 'done'."""
    import copy
    import time
    done = dict(resource='field-cast', action='events', operation_id='field-cast:done:500', attempts=3, at=20,
                events=[dict(k='done', s=500, track=[[0, 0]], pos=[0, 0])])
    refused = dict(ok=False, http_status=521, error='non_json', parse_error='SyntaxError',
                   content_type='text/html; charset=UTF-8', grants=[], cultivation=0, pickedItems=[])
    receipts = [
        dict(resource='quests', action='status', ok=True, at=1),
        dict(resource='quests', action='field-engage', ok=True, operation_id='engage', enemy_id='bamboo:050', at=10),
        dict(resource='field-cast', action='events', ok=True, operation_id='field-cast:begin:500', at=18),
        dict(resource='quests', action='field-defeat', ok=True, operation_id='defeat', enemy_id='bamboo:050', at=19),
        dict(resource='field-cast', action='events', operation_id='field-cast:done:500', at=21, attempt=1, **refused),
        dict(resource='quests', action='status', operation_id=False, at=22, **refused),
        dict(resource='battle/doufa-profile', action='events', ok=False, http_status=None, error='request_aborted', at=30),
        dict(resource='quests', action='status', ok=False, http_status=None, error='request_aborted', at=31),
    ]
    result = dict(player=42, upstream_version='2.0.53', navigation_policy=4, transport_policy=1, phase='held',
                  error='quest_cast_response_unreadable', casts=23, requests=32, pending=2,
                  pending_operations=[done], stored_pending=copy.deepcopy([done]), receipts=receipts,
                  report={'fieldSettlements': [{'ok': True, 'enemyId': 'bamboo:050'}]}, reward_receipts=[])
    previous, flight, observed = {}, {'ok': True, 'mana': {'flightLocked': False}}, time.time()
    if taint == 'identity':
        result['player'] = 43
    elif taint == 'old_transport':
        result['transport_policy'] = 0
    elif taint == 'previous_unverified':
        previous = {'server_checked': False}
    elif taint == 'http_500':
        receipts[4]['http_status'] = 500
    elif taint == 'json_body':
        receipts[4]['content_type'] = 'application/json'
    elif taint == 'use_event':
        done['events'][0]['k'] = 'use'
        result['stored_pending'] = copy.deepcopy([done])
    elif taint == 'two_pending':
        result['pending_operations'] = [done, dict(done, operation_id='field-cast:done:501')]
        result['stored_pending'] = copy.deepcopy(result['pending_operations'])
    elif taint == 'stored_changed':
        result['stored_pending'][0]['at'] += 1
    elif taint == 'begin_missing':
        receipts.pop(2)
    elif taint == 'begin_failed':
        receipts[2]['ok'] = False
    elif taint == 'later_success':
        receipts.append(dict(resource='quests', action='field-defeat', ok=True, operation_id='late', at=25))
    elif taint == 'other_failed_write':
        receipts.append(dict(resource='quests', action='field-defeat', operation_id='lost', at=23, **refused))
    elif taint == 'reward':
        receipts[4]['grants'] = [{'name': '灵石', 'quantity': 1}]
    elif taint == 'failed_settlement':
        result['report']['fieldSettlements'].append({'ok': False})
    elif taint == 'shutdown':
        result['browser_shutdown_error'] = 'TimeoutError'
    elif taint == 'stale_status':
        observed -= 120
    elif taint == 'flight_locked':
        flight['mana']['flightLocked'] = True
    record = _held_record(tmp_path, result, **previous)
    record['observed_at'] = observed
    call, save = AsyncMock(return_value=flight), Mock()
    monkeypatch.setattr(quests, 'save', save)
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is bool(taint)
    assert save.call_count == int(not taint)
    if not taint:
        assert record['phase'] == 'waiting_gameplay'
        assert record['browser_review']['reason'] == 'gateway_refused_cast_completion'
        assert record['browser_review']['abandoned_cast_pending'] == [done]
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')


def test_review_keeps_unresolved_evidence_in_the_record(monkeypatch, tmp_path):
    import json
    pending = [dict(resource='quests', action='field-engage', operation_id='engage', enemy_id='lantern:083', at=10)]
    receipts = [dict(resource='quests', action='status', ok=True, at=1),
                dict(resource='quests', action='field-engage', operation_id='engage', ok=False, http_status=409,
                     error='non_json', at=11)]
    (tmp_path / 'result.json').write_text(json.dumps(dict(
        player=42, phase='held', error='quest_http_409_non_json', pending=1, hp=9000, combat_active=False,
        pending_operations=pending, stored_pending=pending, receipts=receipts)))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {'evidence_dir': str(tmp_path)}}
    monkeypatch.setattr(quests, 'save', Mock())
    asyncio.run(quests.review_browser_hold(AsyncMock(), record, 42))
    evidence = record['browser']['evidence']
    assert record['gameplay_uncertain'] is True
    assert evidence['pending_operations'] == pending and evidence['stored_pending'] == pending
    assert evidence['failed_receipts'] == [receipts[1]]
    assert [row['action'] for row in evidence['last_receipts']] == ['status', 'field-engage']
    assert (evidence['hp'], evidence['combat_active']) == (9000, False)


@pytest.fixture
def native_return_review_case(tmp_path, monkeypatch):
    path = Path(__file__).with_name('fixtures') / 'qingxi-native-health-return-timeout-20261121.json'
    case = json.loads(path.read_text())
    result = case['result']
    now = result['final_status']['at'] + 20
    monkeypatch.setattr(quests.time, 'time', lambda: now)
    record = _held_record(tmp_path, result)
    record['browser'].update(case['current_browser'])
    previous_dir = tmp_path / 'previous'
    previous_dir.mkdir()
    previous_path = previous_dir / 'result.json'
    previous_path.write_text(json.dumps(case['previous_result']))
    record['previous_browser'] = {**case['previous_browser'], 'evidence_dir': str(previous_dir)}
    # The actual response exposes the roster and regions without global
    # active/engagement flags. The review only closes this worker's hold.
    record['field_status_evidence'] = quests.field_status_evidence(
        {'states': {}, 'field': {'enemies': [{'id': 'bamboo:001'}], 'regions': []}},
        record, verified_player_id=42, observed_at=now - .1)
    return record, result, case['previous_result'], previous_path, now


@pytest.fixture
def native_return_cdp_case(tmp_path, monkeypatch):
    fixture = Path(__file__).with_name('fixtures') / 'qingxi-pre-mainline-cdp-return-timeout-20261122.json'
    case = json.loads(fixture.read_text())
    record = case['host_record']
    current_path = tmp_path / 'current' / 'result.json'
    prior_path = tmp_path / 'previous' / 'result.json'
    for path, key in ((current_path, 'result'), (prior_path, 'previous_result')):
        path.parent.mkdir()
        path.write_text(json.dumps(case[key]))
    record.update(browser={**case['current_browser'], 'evidence_dir': str(current_path.parent)},
                  previous_browser={**case['previous_browser'], 'evidence_dir': str(prior_path.parent)},
                  browser_review={**case['browser_review'], 'evidence_dir': str(prior_path.parent)})
    now = case['result']['final_status']['at'] + 90
    monkeypatch.setattr(quests.time, 'time', lambda: now)
    return case, record, current_path, prior_path, now


@pytest.mark.parametrize('fault', [None, 'stale', 'field_active', 'field_engagement', 'unexpected_stop',
                                   'write_receipt', 'old_review_missing', 'old_review_wrong_job',
                                   'old_review_after_entry', 'old_flight_locked', 'old_result_changed',
                                   'new_cleanup_error', 'new_pending', 'new_started', 'flight_locked',
                                   'bad_directory', 'new_worker', 'bad_runtime', 'old_health_changed',
                                   'wrong_identity', 'late_file_change',
                                   'late_review_change', 'save_failed'])
def test_native_return_cdp_hold_requires_new_status_and_closed_original_job(
        native_return_cdp_case, monkeypatch, fault):
    case, record, current_path, prior_path, now = native_return_cdp_case
    result, previous = case['result'], case['previous_result']
    prior_review = copy.deepcopy(record['browser_review'])
    assert not quests.native_return_cdp_reviewable(result, record, 42), 'captured status was pre-run'

    if fault != 'stale':
        record['observed_at'] = now - .1
        record['field_status_evidence']['observed_at'] = now - .2
    if fault in {'field_active', 'field_engagement'}:
        record['field_status_evidence'][fault] = True
    if fault == 'unexpected_stop':
        result['stop_reason']['quest_id'] = 'qx_q20'
        record['browser']['stop_reason']['quest_id'] = 'qx_q20'
    if fault == 'write_receipt':
        result['receipts'][0]['operation_id'] = 'unverified'
        record['browser']['evidence'] = quests.evidence_digest(result)
    if fault == 'new_cleanup_error':
        result['browser_shutdown_error'] = 'TimeoutError'
    if fault == 'new_pending':
        result['stored_pending'] = [{'action': 'field-engage'}]
        record['browser']['evidence'] = quests.evidence_digest(result)
    if fault == 'new_started':
        result['mainline_started_at'] = result['entry_started_at'] + 1
    if fault == 'bad_directory':
        record['browser']['evidence_dir'] = {'path': 'unexpected'}
    if fault == 'new_worker':
        transitions = {'return': {'stage': 'dispatch', 'read_timeouts': 0}}
        result['health_recovery']['transitions'] = transitions
        record['browser']['health_recovery']['transitions'] = transitions
    if fault == 'bad_runtime':
        for report in (result, record['browser']):
            recovery = report['health_recovery']
            for scene in (recovery['initial'], recovery['latest'], *recovery['observations']):
                scene['combat_id'] = {'id': 1}
    if fault == 'old_review_missing':
        del record['browser_review']
    if fault == 'old_review_wrong_job':
        record['browser_review']['evidence_dir'] = str(current_path.parent)
    if fault == 'old_review_after_entry':
        record['browser_review']['at'] = result['entry_started_at'] + 1
    if fault == 'old_flight_locked':
        record['browser_review']['flight']['flightLocked'] = True
    if fault == 'old_result_changed':
        previous['pending_operations'] = [{'action': 'field-engage'}]
        prior_path.write_text(json.dumps(previous))
    if fault == 'old_health_changed':
        previous['health_recovery']['latest']['combat_active'] = True
        prior_path.write_text(json.dumps(previous))
    current_path.write_text(json.dumps(result))
    before = copy.deepcopy(record)

    async def fetch_flight(action, **payload):
        assert action == 'status' and payload == {'_api_path': '/api/miniapp/xianxia-dwelling/flight'}
        if fault == 'late_file_change':
            previous['receipt_edit_after_review'] = True
            prior_path.write_text(json.dumps(previous))
        if fault == 'late_review_change':
            record['browser_review']['at'] += 1
        return {'ok': True, 'playerId': 43 if fault == 'wrong_identity' else 42,
                'mana': {'flightLocked': fault == 'flight_locked'}}

    flight = AsyncMock(side_effect=fetch_flight)
    save = Mock(side_effect=OSError('save failed') if fault == 'save_failed' else None)
    monkeypatch.setattr(quests, 'save', save)
    if fault == 'save_failed':
        with pytest.raises(OSError, match='save failed'):
            asyncio.run(quests.review_browser_hold(flight, record, 42))
        assert record == before
    else:
        asyncio.run(quests.review_browser_hold(flight, record, 42))
        if fault:
            save.assert_not_called()
    if fault in {None, 'flight_locked', 'wrong_identity', 'late_file_change', 'late_review_change', 'save_failed'}:
        flight.assert_awaited_once()
    else:
        flight.assert_not_awaited()
    assert record['gameplay_uncertain'] is bool(fault)
    assert record['phase'] == ('held' if fault else 'waiting_gameplay')
    if fault is None:
        save.assert_called_once()
        assert record['browser_review']['reason'] == quests.NATIVE_RETURN_CDP_REVIEW
        assert record['browser_review']['previous_review'] == prior_review
        assert record['field_status_evidence']['field_active'] == 'unknown'
        assert record['field_status_evidence']['field_engagement'] == 'unknown'
        assert json.loads(current_path.read_text()) == result


@pytest.mark.parametrize('scope, field, value', [
    ('', '', None),
    ('result', 'player', 43),
    ('result', 'workflow', 'rewards'),
    ('result', 'phase', 'running'),
    ('result', 'upstream_version', 'unknown'),
    ('result', 'upstream_version', []),
    ('result', 'transport_policy', 0),
    ('result', 'mainline_started_at', 1),
    ('result', 'run', {'done': False}),
    ('result', 'pending_operations', [{'action': 'gather-start'}]),
    ('result', 'stored_pending', [{'action': 'gather-finish'}]),
    ('result', 'casts', 1),
    ('result', 'casts', False),
    ('result', 'pending', False),
    ('result', 'requests', 5),
    ('result', 'reward_receipts', [{'grants': [1]}]),
    ('result', 'entry_error', 'other_error'),
    ('result', 'cleanup_error', 'TimeoutError'),
    ('result', 'browser_shutdown_error', 'TimeoutError'),
    ('result', 'browser_close_error', 'TimeoutError'),
    ('browser', 'error', 'other_error'),
    ('browser', 'workflow', 'rewards'),
    ('browser', 'server_checked', False),
    ('browser', 'cleanup_error', 'TimeoutError'),
    ('browser', 'version', '2.0.53'),
    ('browser', 'evidence_dir', []),
    ('record', 'gather', {'gatherId': 'old'}),
    ('record', 'pending', {'operationId': 'old'}),
    ('record', 'previous_browser', None),
    ('previous', 'flight_checked', False),
    ('previous', 'evidence_dir', []),
    ('previous', 'error', 'other_error'),
    ('previous', 'final_status', {'fresh': False}),
    ('previous_result', 'player', 43),
    ('previous_result', 'server_checked', False),
    ('previous_result', 'stored_pending', [{'action': 'field-engage'}]),
    ('previous_result', 'combat_active', True),
    ('previous_result', 'browser_shutdown_error', 'TimeoutError'),
    ('initial', 'combat_active', True),
    ('initial', 'combat_matches_runtime', False),
    ('initial', 'state', 'defeat'),
    ('initial', 'overworld', True),
    ('initial', 'flight_active', True),
    ('initial', 'hp', 1),
    ('initial', 'runtime_id', 7),
    ('initial', 'combat_id', 1),
    ('home', 'error', '等待超时：加载入口'),
    ('home', 'verified', True),
    ('scene', 'scene_ready', True),
    ('scene', 'scene_tier', 4),
    ('scene', 'flight_active', True),
    ('scene', 'chariot_mounted', True),
    ('scene', 'ghost', True),
    ('scene', 'hp', 1),
    ('scene', 'hp', 0),
    ('scene', 'hp', float('nan')),
    ('scene', 'hp_max', float('inf')),
    ('scene', 'combat_present', False),
    ('scene', 'combat_matches_runtime', False),
    ('scene', 'engaged_enemies', False),
    ('receipt', 'action', 'gather-finish'),
    ('receipt', 'operation_id', 'write-operation'),
    ('receipt', 'ok', False),
    ('receipt', 'at', 1),
    ('final', 'fresh', False),
    ('fresh', 'observed_at', 1_699_999_999),
    ('fresh', 'verified_player_id', 43),
    ('fresh', 'field_active', True),
    ('fresh', 'field_engagement', True),
    ('fresh', 'field_engagement', {'present': True, 'type': 'object', 'active': True}),
    ('flight', 'mana', {'flightLocked': True}),
    ('flight', 'playerId', 43),
])
def test_review_native_health_return_timeout_requires_complete_evidence(
        monkeypatch, tmp_path, native_return_review_case, scope, field, value):
    record, result, prior, prior_path, now = native_return_review_case
    flight = {'ok': True, 'mana': {'flightLocked': False}}
    if scope:
        target = {
            'result': result, 'browser': record['browser'], 'record': record,
            'home': result['health_recovery']['latest']['home'], 'receipt': result['receipts'][-1],
            'initial': result['health_recovery']['initial'], 'scene': result['health_recovery']['latest'],
            'previous': record['previous_browser'], 'previous_result': prior,
            'final': result['final_status'], 'fresh': record['field_status_evidence'], 'flight': flight,
        }[scope]
        target[field] = value
        (tmp_path / 'result.json').write_text(json.dumps(result))
        prior_path.write_text(json.dumps(prior))
    persisted = (tmp_path / 'result.json').read_bytes()
    call, save = AsyncMock(return_value=flight), Mock()
    monkeypatch.setattr(quests, 'save', save)

    asyncio.run(quests.review_browser_hold(call, record, 42))

    if scope in {'', 'flight'}:
        call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    else:
        call.assert_not_awaited()
    assert record['gameplay_uncertain'] is bool(scope)
    assert record['phase'] == ('held' if scope else 'waiting_gameplay')
    assert (tmp_path / 'result.json').read_bytes() == persisted
    if scope:
        save.assert_not_called()
        assert 'browser_review' not in record
    else:
        save.assert_called_once()
        assert record['next_at'] == now + 60
        assert record['browser_review'] == {
            'at': now, 'evidence_dir': str(tmp_path), 'reason': 'pre_mainline_read_only_render_failure',
            'flight': {'ok': True, 'flightLocked': False},
        }
        assert record['field_status_evidence']['field_active'] == 'unknown'
        assert record['field_status_evidence']['field_engagement'] == 'unknown'


@pytest.mark.parametrize('value', [None, [], {'gameplay_uncertain': True},
                                  {'gameplay_uncertain': True, 'browser': []}])
def test_native_return_review_rejects_missing_or_malformed_record(monkeypatch, value):
    save = Mock()
    monkeypatch.setattr(quests, 'save', save)
    call = AsyncMock()
    assert not quests.native_health_return_reviewable({}, value, 42)
    asyncio.run(quests.review_browser_hold(call, value, 42))
    call.assert_not_awaited()
    save.assert_not_called()


@pytest.mark.parametrize('taint', [
    'evidence_dir', 'browser', 'late_pending', 'stale', 'current_file', 'previous_file', 'save',
])
def test_native_return_review_rechecks_after_flight_and_rolls_back_failed_save(
        monkeypatch, tmp_path, native_return_review_case, taint):
    record, result, prior, prior_path, _now = native_return_review_case
    record['browser']['evidence'] = {}
    before = copy.deepcopy(record)

    async def flight(action, **payload):
        assert action == 'status' and payload == {'_api_path': '/api/miniapp/xianxia-dwelling/flight'}
        if taint == 'evidence_dir':
            record['browser']['evidence_dir'] = str(tmp_path / 'replacement')
        elif taint == 'browser':
            record['browser'] = copy.deepcopy(record['browser'])
        elif taint == 'late_pending':
            record['pending'] = {'operationId': 'other'}
        elif taint == 'stale':
            record['field_status_evidence']['observed_at'] -= 60
        elif taint == 'current_file':
            result['receipt_edit_after_review'] = True
            (tmp_path / 'result.json').write_text(json.dumps(result))
        elif taint == 'previous_file':
            prior['server_checked'] = False
            prior_path.write_text(json.dumps(prior))
        return {'ok': True, 'mana': {'flightLocked': False}}

    save = Mock(side_effect=OSError('save failed') if taint == 'save' else None)
    monkeypatch.setattr(quests, 'save', save)
    if taint == 'save':
        with pytest.raises(OSError, match='save failed'):
            asyncio.run(quests.review_browser_hold(flight, record, 42))
        assert record == before
    else:
        asyncio.run(quests.review_browser_hold(flight, record, 42))
        save.assert_not_called()
    assert record['gameplay_uncertain'] is True and record['phase'] == 'held'
    assert 'browser_review' not in record
