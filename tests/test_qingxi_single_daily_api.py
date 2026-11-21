"""Explicit manual single-daily API scope; no browser or game requests."""
import asyncio
import copy
from unittest.mock import Mock

import pytest

from model.features import qingxi_quests as quests


DAILY_IDS = ('moonpalace', 'bloodland', 'southgate', 'escort',
             'mulanshrine', 'bqpuppet', 'jianzhong')


@pytest.fixture
def api_environment(monkeypatch):
    record = {'phase': 'idle', 'reward_batch': {'phase': 'partial'}, 'rewards_next_at': 12345}
    seen = []

    async def run_once(identity_id, **kwargs):
        seen.append((identity_id, kwargs))

    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, '_TASKS', {})
    monkeypatch.setattr(quests, 'run_once', run_once)
    monkeypatch.setattr(quests, 'track_background_task', lambda task: task)
    return record, seen


@pytest.mark.parametrize('daily_id', DAILY_IDS)
@pytest.mark.parametrize('action,status_only', [('reward_one', False), ('reward_one_refresh', True)])
def test_single_daily_api_queues_only_an_explicit_whitelisted_scope(api_environment, daily_id, action, status_only):
    record, seen = api_environment
    before = copy.deepcopy(record)

    async def scenario():
        ok, _message, _result = await quests.action({
            'action': action, 'send_as_id': 42, 'daily_id': daily_id,
        })
        assert ok
        await asyncio.gather(*list(quests._TASKS.values()))

    asyncio.run(scenario())
    assert seen == [(42, {'status_only': status_only, 'reward_batch': True, 'daily_id': daily_id})]
    assert record == before


@pytest.mark.parametrize('daily_id', [None, '', ' moonpalace', 'moonpalace ', 'MOONPALACE',
                                     'longwind', 'bamboo', 'all', '../moonpalace',
                                     ['moonpalace'], {'id': 'moonpalace'}, 1, True])
@pytest.mark.parametrize('action', ['reward_one', 'reward_one_refresh'])
def test_single_daily_api_rejects_missing_unknown_or_non_scalar_slot(api_environment, daily_id, action):
    record, seen = api_environment
    before = copy.deepcopy(record)
    payload = {'action': action, 'send_as_id': 42}
    if daily_id is not None:
        payload['daily_id'] = daily_id
    ok, _message, _result = asyncio.run(quests.action(payload))
    assert not ok
    assert seen == [] and quests._TASKS == {}
    assert record == before


@pytest.mark.parametrize('action', ['status', 'run', 'refresh', 'rewards', 'rewards_refresh', 'operator_release'])
@pytest.mark.parametrize('extra', [{'daily_id': 'moonpalace'}, {'daily_id': None}, {'slot': 'moonpalace'}])
def test_old_actions_never_silently_ignore_single_daily_fields(api_environment, action, extra):
    record, seen = api_environment
    before = copy.deepcopy(record)
    ok, _message, _result = asyncio.run(quests.action({'action': action, 'send_as_id': 42, **extra}))
    assert not ok
    assert seen == [] and quests._TASKS == {}
    assert record == before


@pytest.mark.parametrize('action', ['reward_one', 'reward_one_refresh'])
def test_single_daily_rejects_an_ambiguous_second_slot_field(api_environment, action):
    _record, seen = api_environment
    ok, _message, _result = asyncio.run(quests.action({
        'action': action, 'send_as_id': 42, 'daily_id': 'moonpalace', 'slot': 'bloodland',
    }))
    assert not ok and not seen and not quests._TASKS


@pytest.mark.parametrize('action,status_only,reward_batch', [
    ('run', False, False), ('refresh', True, False),
    ('rewards', False, True), ('rewards_refresh', True, True),
])
def test_existing_actions_keep_their_original_unscoped_contract(
        api_environment, action, status_only, reward_batch):
    _record, seen = api_environment

    async def scenario():
        ok, _message, _result = await quests.action({'action': action, 'send_as_id': 42})
        assert ok
        await asyncio.gather(*list(quests._TASKS.values()))

    asyncio.run(scenario())
    assert seen == [(42, {'status_only': status_only, 'reward_batch': reward_batch})]


@pytest.mark.parametrize('action', ['reward_one', 'reward_one_refresh'])
def test_an_existing_quest_task_prevents_single_daily_enqueue(api_environment, action):
    _record, seen = api_environment
    existing_task = object()
    quests._TASKS[99] = existing_task
    ok, message, _result = asyncio.run(quests.action({
        'action': action, 'send_as_id': 42, 'daily_id': 'moonpalace',
    }))
    assert not ok and '执行中' in message
    assert not seen and quests._TASKS == {99: existing_task}


@pytest.mark.parametrize('action', ['reward_single', 'reward_one_all', 'reward_one_resume'])
def test_future_or_unknown_single_daily_actions_do_not_enqueue(api_environment, action):
    _record, seen = api_environment
    ok, _message, _result = asyncio.run(quests.action({
        'action': action, 'send_as_id': 42, 'daily_id': 'moonpalace',
    }))
    assert not ok and not seen and not quests._TASKS
