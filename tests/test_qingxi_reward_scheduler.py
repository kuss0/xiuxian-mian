"""Daily ownership, conservative review and truthful thirteen-slot progress."""
import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from model import app_expeditions, state
from model.features import cave_treasure_runtime as cave, changfeng_trial as trial
from model.features import qingxi_quests as quests, qingxi_rewards as rewards, qingxi_trial_browser as browser


@pytest.fixture
def environment(monkeypatch):
    previous = copy.deepcopy(state._meta_state)
    state.set_expedition_run_state({})
    state.set_expedition_config({})
    config = {'qingxi_rewards': {'enabled': True, 'participant_identity_ids': [42]}}
    monkeypatch.setattr(rewards, 'get_expedition_config', lambda: config)
    monkeypatch.setattr(rewards, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(rewards, '_entry_url', lambda: 'fixture')
    monkeypatch.setattr(rewards, 'is_cave_public_identity_available', lambda _id: True)
    monkeypatch.setattr(rewards, 'get_activity_block_reason', lambda _id, **_kw: '')
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, '_TASKS', {})
    action = AsyncMock(return_value=(True, 'scheduled', {}))
    notice = AsyncMock(return_value=True)
    monkeypatch.setattr(quests, 'action', action)
    monkeypatch.setattr(rewards, 'send_audit_log', notice)
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _id: asyncio.Lock())
    yield config, action, notice
    state._meta_state.clear()
    state._meta_state.update(previous)


def test_reward_scheduler_reserves_before_enqueue_and_does_not_repeat(environment):
    _config, action, _notice = environment
    asyncio.run(rewards.scheduler(100))
    action.assert_awaited_once_with({'action': 'rewards', 'send_as_id': 42})
    assert quests.records()['42']['rewards_next_at'] == 1900
    asyncio.run(rewards.scheduler(101))
    assert action.await_count == 1


@pytest.mark.parametrize('cause', ['disabled', 'paused', 'offline', 'active_task', 'future', 'invalid_due'])
def test_reward_scheduler_respects_all_existing_gates(environment, monkeypatch, cause):
    config, action, _notice = environment
    if cause == 'disabled':
        config['qingxi_rewards']['enabled'] = False
    elif cause == 'paused':
        monkeypatch.setattr(rewards, 'get_global_enabled', lambda: False)
    elif cause == 'offline':
        monkeypatch.setattr(rewards, 'is_cave_public_identity_available', lambda _id: False)
    elif cause == 'active_task':
        quests._TASKS[42] = object()
    else:
        quests.records()['42'] = {'rewards_next_at': 10000 if cause == 'future' else float('nan')}
    asyncio.run(rewards.scheduler(100))
    action.assert_not_awaited()


@pytest.mark.parametrize('reward_workflow', [False, True])
def test_unknown_gameplay_is_only_reviewed_and_block_notice_is_deduplicated(environment, reward_workflow):
    _config, action, notice = environment
    record = {'phase': 'held', 'gameplay_uncertain': True,
              'browser': {'workflow': 'rewards' if reward_workflow else 'quests'}}
    quests.records()['42'] = record
    asyncio.run(rewards.scheduler(100))
    assert record['gameplay_uncertain'] is True and record['phase'] == 'held'
    if reward_workflow:
        action.assert_awaited_once_with({'action': 'rewards_refresh', 'send_as_id': 42})
    else:
        action.assert_not_awaited()
    asyncio.run(rewards.scheduler(1901))
    assert notice.await_count == 1
    assert record['rewards_block_notice']['state'] == 'sent'


def test_new_scheduler_ownership_excludes_old_automatic_trial_paths(environment, monkeypatch):
    config, _action, _notice = environment
    config['changfeng'] = {'enabled': True, 'participant_identity_ids': [42]}
    monkeypatch.setattr(trial, 'get_expedition_config', lambda: config)
    monkeypatch.setattr(trial, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(trial, '_entry_url', lambda: 'fixture')
    monkeypatch.setattr(trial, '_TASKS', {})
    scheduled = AsyncMock()
    monkeypatch.setattr(browser, 'run_scheduled_once', scheduled)
    asyncio.run(trial.run_changfeng_scheduler(100))
    scheduled.assert_not_awaited()


def test_confirmed_daily_defeat_preserves_remaining_slots(environment):
    _config, action, notice = environment
    trial._records()['42'] = {'browser_trial': {'kind': 'defeat', 'server_closed': True,
                                               'rewarded': True, 'finished_at': 50}}
    asyncio.run(rewards.scheduler(100))
    action.assert_not_awaited()
    assert quests.records()['42']['rewards_next_at'] > 3600
    assert '保留剩余奖励次数' in notice.call_args.args[0]


def test_batch_stop_survives_readonly_review_rescheduling(environment):
    _config, action, notice = environment
    quests.records()['42'] = {'phase': 'idle', 'rewards_next_at': 0, 'reward_batch': {
        'stop_for_day': True, 'before': {'day': rewards._day(100)}}}
    asyncio.run(rewards.scheduler(100))
    action.assert_not_awaited()
    assert '结算需复核' in notice.call_args.args[0]
    assert quests.records()['42']['rewards_next_at'] > 3600


def test_failure_review_pause_is_not_erased_by_calendar_rollover(environment):
    _config, action, _notice = environment
    trial._records()['42'] = {'auto_pause': {'reason': 'confirmed_defeat', 'run_id': 'old-loss'}}
    asyncio.run(rewards.scheduler(100000))
    action.assert_not_awaited()
    assert rewards.snapshots()['42']['auto_paused'] is True


def test_direct_queued_trial_cannot_bypass_new_scheduler_ownership(environment):
    ok, message = asyncio.run(browser.run_scheduled_once(42))
    assert not ok and '接管' in message


def test_progress_has_thirteen_slots_and_distinguishes_consumption_from_victory(environment):
    rows = [{'id': key, 'claimed': False, 'hasKey': False} for key in rewards.DAILY]
    record = {'phase': 'idle', 'reward_batch': {'phase': 'partial', 'before': {'day': '2026-11-20',
        'daily': rows, 'trials': {'longwind': {'used': 1}, 'bamboo': {'used': 0}}}}}
    progress = rewards.progress(record)
    assert len(progress['slots']) == 13
    assert progress['claimed'] == 0 and progress['trial_used'] == 1
    assert progress['victories_this_batch'] == 0
    assert [row['state'] for row in progress['slots'][:7]] == ['locked'] * 7
    assert progress['slots'][7]['state'] == 'used'
    record.update(phase='running', gameplay_uncertain=True)
    assert rewards.progress(record)['phase'] == 'running'
    record['phase'] = 'held'
    assert rewards.progress(record)['phase'] == 'held'


def test_configured_unstarted_identity_has_thirteen_unknown_slots(environment):
    snapshot = rewards.snapshots()['42']
    assert len(snapshot['slots']) == 13
    assert all(row['state'] == 'unknown' for row in snapshot['slots'])


@pytest.mark.parametrize('bad', [True, [], {'enabled': 'false'}, {'enabled': True, 'participant_identity_ids': [True]},
                                 {'enabled': True, 'participant_identity_ids': [99]}])
def test_config_rejects_unregistered_identities_and_invalid_switches(environment, monkeypatch, bad):
    monkeypatch.setattr(app_expeditions, 'get_identity_ids', lambda: [42])
    ok, _message = app_expeditions.ui_set_expedition_config({'qingxi_rewards': bad})
    assert not ok


def test_partial_config_save_preserves_unrelated_npc_choices(environment, monkeypatch):
    state.set_expedition_config({'qingxi_quests': {'enabled': True, 'choices': {'q': 'keep'}}})
    monkeypatch.setattr(app_expeditions, 'get_identity_ids', lambda: [42])
    monkeypatch.setattr(app_expeditions, 'save_state', Mock(return_value=True))
    ok, _message = app_expeditions.ui_set_expedition_config({'qingxi_rewards': {
        'enabled': False, 'participant_identity_ids': [42]}})
    assert ok
    saved = state.get_expedition_config()
    assert saved['qingxi_quests']['choices'] == {'q': 'keep'}
    assert saved['qingxi_rewards'] == {'enabled': False, 'participant_identity_ids': [42]}
