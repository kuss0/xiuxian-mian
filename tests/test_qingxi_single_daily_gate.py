"""Daily defeat gates precede entry and preserve unrelated manual ownership."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import cave_treasure_runtime as cave, changfeng_trial as trial
from model.features import qingxi_quests as quests, qingxi_rewards as rewards
from test_qingxi_single_daily_proof import claimed_result


NOW = datetime(2026, 11, 21, 12, tzinfo=timezone(timedelta(hours=8))).timestamp()


def failed_daily(day='2026-11-21', daily_id='bloodland'):
    result = claimed_result(daily_id)
    proof = result['reward_one']
    proof.update(phase='partial', outcome='defeat')
    proof['receipts'][-1].update(request_result='defeat', result_kind='failure',
                                  result_rewarded=False, grants=[], cultivation=0)
    for field in ('before', 'final'):
        snapshot = proof[field]
        snapshot['day'] = day
        for item in snapshot['daily']:
            item['day'] = day
            if item['id'] == daily_id:
                item['hasKey'] = True
                item['claimed'] = False
        for quota in snapshot['trials'].values():
            quota['day'] = day
    return proof


@pytest.fixture
def gates(monkeypatch):
    record = {'phase': 'idle', 'rewards_next_at': 0, 'reward_one_runs': {'bloodland': failed_daily()}}
    records = {'42': record}
    config = {'qingxi_rewards': {'enabled': True, 'participant_identity_ids': [42]},
              'qingxi_quests': {'enabled': False}}
    owner = SimpleNamespace(identity_id=42, account_id=42, is_current=lambda: True)
    monkeypatch.setattr(quests.time, 'time', lambda: NOW)
    monkeypatch.setattr(quests, 'records', lambda: records)
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, '_TASKS', {})
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', lambda _: owner)
    monkeypatch.setattr(trial, '_records', lambda: {})
    for module in (quests, rewards):
        monkeypatch.setattr(module, 'get_global_enabled', lambda: True)
        monkeypatch.setattr(module, 'get_expedition_config', lambda: config)
        monkeypatch.setattr(module, 'get_activity_block_reason', lambda *_args, **_kwargs: '')
        monkeypatch.setattr(module, 'is_cave_public_identity_available', lambda _: True)
        monkeypatch.setattr(module, '_entry_url', lambda: 'fixture-entry')
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, '_public_entry_allowed', lambda: True)
    monkeypatch.setattr(cave, '_parse_public_cave_entry_url', lambda _: ('fixture-token', 'fixture-url', ''))
    entry = AsyncMock(side_effect=RuntimeError('fixture_entry_reached'))
    browser = AsyncMock(side_effect=AssertionError('this test must not launch a browser'))
    notice = AsyncMock(return_value=True)
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', entry)
    monkeypatch.setattr(quests, 'run_gameplay', browser)
    monkeypatch.setattr(quests, 'send_audit_log', notice)
    monkeypatch.setattr(rewards, 'send_audit_log', notice)
    return SimpleNamespace(record=record, records=records, entry=entry, browser=browser, notice=notice)


def test_full_manual_batch_stops_before_entry_after_nonmoon_daily_defeat(gates):
    # Bloodland can remain hasKey/unclaimed after losing; those facts cannot
    # override the recorded defeat and trigger another batch attempt today.
    initial = copy.deepcopy(gates.record)
    ok, message = asyncio.run(quests.run_once(42, reward_batch=True))
    assert not ok and '未通关' in message
    assert gates.record == initial
    gates.entry.assert_not_awaited()
    gates.browser.assert_not_awaited()


def test_full_scheduler_stops_after_same_day_nonmoon_daily_defeat(gates, monkeypatch):
    enqueue = AsyncMock(return_value=(True, 'queued', {}))
    monkeypatch.setattr(quests, 'action', enqueue)
    asyncio.run(rewards.scheduler(NOW))
    enqueue.assert_not_awaited()
    gates.entry.assert_not_awaited()
    gates.browser.assert_not_awaited()
    assert rewards._day(gates.record['rewards_next_at']) == '2026-11-22'
    assert gates.record['reward_one_runs']['bloodland']['outcome'] == 'defeat'


def test_prior_day_daily_defeat_does_not_block_today_scheduler(gates, monkeypatch):
    gates.record['reward_one_runs']['bloodland'] = failed_daily('2026-11-20')
    enqueue = AsyncMock(return_value=(True, 'queued', {}))
    monkeypatch.setattr(quests, 'action', enqueue)
    assert rewards.defeated_today(42, NOW) is False
    asyncio.run(rewards.scheduler(NOW))
    enqueue.assert_awaited_once_with({'action': 'rewards', 'send_as_id': 42})
    gates.entry.assert_not_awaited()
    gates.browser.assert_not_awaited()


def test_prior_day_daily_defeat_does_not_block_manual_batch_before_entry(gates):
    gates.record['reward_one_runs']['bloodland'] = failed_daily('2026-11-20')
    ok, message = asyncio.run(quests.run_once(42, reward_batch=True))
    assert not ok and message == 'fixture_entry_reached'
    gates.entry.assert_awaited_once()
    gates.browser.assert_not_awaited()


def test_today_defeat_is_scoped_to_the_identity(gates, monkeypatch):
    gates.records['43'] = gates.records.pop('42')
    gates.records['42'] = {'phase': 'idle', 'rewards_next_at': 0}
    enqueue = AsyncMock(return_value=(True, 'queued', {}))
    monkeypatch.setattr(quests, 'action', enqueue)
    assert rewards.defeated_today(42, NOW) is False
    asyncio.run(rewards.scheduler(NOW))
    enqueue.assert_awaited_once_with({'action': 'rewards', 'send_as_id': 42})


def test_same_daily_manual_attempt_stops_on_the_defeat_day(gates):
    initial = copy.deepcopy(gates.record)
    ok, message = asyncio.run(quests.run_once(42, reward_batch=True, daily_id='bloodland'))
    assert not ok and '今日已确认战败' in message
    assert gates.record == initial
    gates.entry.assert_not_awaited()
    gates.browser.assert_not_awaited()


def test_a_different_manual_daily_can_reach_the_normal_entry_gate(gates):
    ok, message = asyncio.run(quests.run_once(42, reward_batch=True, daily_id='moonpalace'))
    assert not ok and message == 'fixture_entry_reached'
    gates.entry.assert_awaited_once()
    gates.browser.assert_not_awaited()
    assert gates.record['reward_one_runs']['bloodland']['outcome'] == 'defeat'


@pytest.mark.parametrize('gate', ['global_pause', 'owner_missing', 'offline', 'activity', 'entry_busy', 'unknown'])
def test_other_manual_daily_still_respects_existing_ownership_gates(gates, monkeypatch, gate):
    if gate == 'global_pause':
        monkeypatch.setattr(quests, 'get_global_enabled', lambda: False)
    elif gate == 'owner_missing':
        monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', lambda _: None)
    elif gate == 'offline':
        monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: False)
    elif gate == 'activity':
        monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *_args, **_kwargs: '组队活动执行中')
    elif gate == 'entry_busy':
        monkeypatch.setattr(cave, '_public_entry_lock', lambda _: SimpleNamespace(locked=lambda: True))
    else:
        gates.record.update(phase='held', gameplay_uncertain=True)
    ok, _message = asyncio.run(quests.run_once(42, reward_batch=True, daily_id='moonpalace'))
    assert not ok
    gates.entry.assert_not_awaited()
    gates.browser.assert_not_awaited()
    assert gates.record['reward_one_runs']['bloodland']['outcome'] == 'defeat'
