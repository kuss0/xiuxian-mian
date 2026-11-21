import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import qingxi_quests as quests


Q23 = 'qx_q23'
BOTTLE_NODE = {'id': 'bottle:test', 'itemId': 'qxq_sealing_bottle', 'position': [40, 60]}


def reply(states, nodes=None):
    return {'ok': True, 'quests': {
        'world': {'current': 'yue'}, 'states': states, 'nodes': [BOTTLE_NODE] if nodes is None else nodes,
    }}


@pytest.mark.parametrize(('slot_fields', 'reason', 'eligible'), [
    pytest.param({}, 'timed_bottle_requires_browser', True, id='unstarted'),
    pytest.param({'timers': {}, 'progress': {'bottle': 0}},
                 'timed_bottle_requires_browser', True, id='empty-server-state'),
    pytest.param({'timers': {'bottle': 2_000_000_300}},
                 'timed_bottle_state_requires_review', False, id='running-timer'),
    pytest.param({'timers': {'bottle': 1}},
                 'timed_bottle_state_requires_review', False, id='apparently-expired-timer'),
    pytest.param({'timers': None}, 'timed_bottle_state_requires_review', False, id='unknown-timer'),
    pytest.param({'progress': {'bottle': 1}},
                 'timed_bottle_state_requires_review', False, id='completed-objective-still-active'),
    pytest.param({'progress': {'bottle': False}},
                 'timed_bottle_state_requires_review', False, id='boolean-progress'),
    pytest.param({'progress': {'bottle': '0'}},
                 'timed_bottle_state_requires_review', False, id='string-progress'),
    pytest.param({'progress': None}, 'timed_bottle_state_requires_review', False, id='unknown-progress'),
])
def test_q23_only_fresh_server_state_can_reserve_a_browser_fill(monkeypatch, slot_fields, reason, eligible):
    monkeypatch.setattr(quests.time, 'time', lambda: 2_000_000_000)
    call = AsyncMock(return_value=reply({Q23: {'status': 'active', **slot_fields}}))
    record = {'observed': {Q23: {'status': 'active', 'progress': {'bottle': 0}}}, 'observed_at': 1}

    asyncio.run(quests.run_protocol(call, record, Mock()))

    call.assert_awaited_once_with('status')
    assert record['available_nodes'][0]['id'] == BOTTLE_NODE['id']
    assert [gap['reason'] for gap in record['gaps']] == [reason]
    assert quests.browser_required(record['gaps']) is eligible
    assert not record.get('pending') and not record.get('gather') and not record.get('receipts')


def test_q23_becomes_eligible_for_browser_fill_only_after_server_clears_expired_timer():
    call = AsyncMock(side_effect=[
        reply({Q23: {'status': 'active', 'timers': {'bottle': 1}}}),
        reply({Q23: {'status': 'active', 'timers': {}, 'progress': {'bottle': 0}}}),
    ])
    record = {}

    asyncio.run(quests.run_protocol(call, record, Mock()))
    assert not quests.browser_required(record['gaps'])
    asyncio.run(quests.run_protocol(call, record, Mock()))
    assert quests.browser_required(record['gaps'])
    assert [request.args for request in call.await_args_list] == [('status',), ('status',)]


def test_q23_fresh_ready_turns_in_ahead_of_q22_and_ignores_an_old_timer(monkeypatch):
    monkeypatch.setattr(quests.time, 'time', lambda: 2_000_000_000)
    start = {Q23: {'status': 'ready', 'timers': {'bottle': 1}, 'progress': {'bottle': 1}},
             'qx_q22': {'status': 'ready'}}
    record = {'observed': {Q23: {'status': 'active', 'progress': {'bottle': 0}}}}
    after_q23 = {**start, Q23: {'status': 'rewarded'}}
    after_both = {**after_q23, 'qx_q22': {'status': 'rewarded'}}
    call = AsyncMock(side_effect=[reply(start), reply(after_q23), reply(after_both)])

    asyncio.run(quests.run_protocol(call, record, Mock()))

    assert [(request.args[0], request.kwargs.get('questId')) for request in call.await_args_list] == [
        ('status', None), ('turn-in', Q23), ('turn-in', 'qx_q22'),
    ]
    assert call.await_args_list[1].kwargs['npcId'] == 'herbalist'
    assert record['observed'][Q23]['status'] == 'rewarded'


def test_q23_retry_reconciles_an_already_rewarded_turn_in_without_replay():
    record = {'observed': {Q23: {'status': 'ready'}},
              'pending': {'action': 'turn-in', 'questId': Q23, 'operationId': 'earlier-turn-in'}}
    call = AsyncMock(return_value=reply({Q23: {'status': 'rewarded'}}))

    asyncio.run(quests.run_protocol(call, record, Mock()))

    call.assert_awaited_once_with('status')
    assert 'pending' not in record
    assert record['receipts'][0]['operation_id'] == 'earlier-turn-in'
    assert record['receipts'][0]['reconciled_from_status'] is True


def test_q23_review_gap_allows_an_ordinary_host_gather():
    active = {Q23: {'status': 'active', 'timers': {'bottle': 1}},
              'qx_q02': {'status': 'active', 'required': {'herbs': 1}}}
    done = {**active, 'qx_q02': {'status': 'rewarded'}}
    herb = {'id': 'herb:test', 'itemId': 'qxq_fresh_herb', 'position': [62, 78]}
    start = reply(active, nodes=[BOTTLE_NODE, herb])
    call = AsyncMock(side_effect=[start, {**start, 'gatherId': 'herb-gather', 'duration': 8}, reply(done)])
    sleep, record = AsyncMock(), {}

    asyncio.run(quests.run_protocol(call, record, Mock(), sleeper=sleep))

    assert [(request.args[0], request.kwargs.get('nodeId')) for request in call.await_args_list] == [
        ('status', None), ('gather-start', 'herb:test'), ('gather-finish', 'herb:test'),
    ]
    assert call.await_args_list[-1].kwargs['gatherId'] == 'herb-gather'
    sleep.assert_awaited_once_with(8.5)
    assert [gap['reason'] for gap in record['gaps']] == ['timed_bottle_state_requires_review']
    assert quests.browser_required(record['gaps']) is False
