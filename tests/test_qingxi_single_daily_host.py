"""Host ownership, flight closure and scoped notification without live requests."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import qingxi_quests as quests
from test_qingxi_single_daily_proof import claimed_result


@pytest.mark.parametrize('taint', ['', 'wrong_run', 'missing_flight', 'flight_locked', 'unknown', 'journal'])
def test_single_worker_keeps_ownership_until_same_run_and_fresh_flight(monkeypatch, tmp_path, taint):
    from model.features import cave_treasure_miniapp, qingxi_trial_browser
    for name in ('.venv/bin/python', 'tools/longwind_trial_bot.py'):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    monkeypatch.setattr(qingxi_trial_browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(cave_treasure_miniapp, 'request_cave_treasure_miniapp_init_data', AsyncMock(return_value='fixture'))
    record = {'reward_batch': {'phase': 'partial', 'day': 'previous'}, 'rewards_next_at': 12345}
    before = copy.deepcopy(record)
    saved, commands = [], []
    monkeypatch.setattr(quests, 'save', lambda: saved.append(copy.deepcopy(record)))
    monkeypatch.setattr(quests, '_drain_browser_process', AsyncMock())
    result = claimed_result()
    result['phase'] = 'done'
    if taint == 'wrong_run':
        result['reward_one']['receipts'][1]['run_id'] = 'other-run'
    elif taint == 'unknown':
        result['server_checked'] = False
        result['reward_one']['phase'] = 'held'
    elif taint == 'journal':
        result['stored_pending'] = [{'operation_id': 'original-unknown'}]
    async def spawn(*command, **kwargs):
        assert saved[-1]['gameplay_uncertain'] is True
        assert saved[-1]['browser']['daily_id'] == 'moonpalace'
        assert kwargs['env']['XIUXIAN_FANREN_PROCESS_OWNER']
        commands.append(command)
        Path(command[command.index('--result') + 1]).write_text(json.dumps(result))
        return SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))
    monkeypatch.setattr(quests.asyncio, 'create_subprocess_exec', spawn)
    flight = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': taint == 'flight_locked'}})
    if taint == 'missing_flight':
        flight.side_effect = RuntimeError('unconfirmed flight')
    operation = quests.run_gameplay(SimpleNamespace(identity_id=42), 'fixture', 'fixture', record, lambda: True,
        verify_flight=flight, reward_batch=True, daily_id='moonpalace')
    if not taint:
        asyncio.run(operation)
        assert record['gameplay_uncertain'] is False
        assert record['browser']['flight_checked'] is True
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(operation)
        assert record['gameplay_uncertain'] is True
    assert record['reward_batch'] == before['reward_batch'] and record['rewards_next_at'] == before['rewards_next_at']
    assert 'reward_batch' not in record['browser']
    command = commands[0]
    assert command[command.index('--daily-id') + 1] == 'moonpalace'
    assert command[command.index('--max-seconds') + 1] == '3600'
    assert not list(tmp_path.glob('.runtime/**/launch.url'))
    assert flight.await_count == (0 if taint in {'wrong_run', 'unknown', 'journal'} else 1)


@pytest.mark.parametrize('delivery', [True, False, 'exception'])
def test_single_notice_is_exact_run_scoped_durable_and_never_replayed(monkeypatch, delivery):
    result = claimed_result()
    proof = result['reward_one']
    proof['receipts'][-1]['reward_id'] = 'moon-palace:fixture-run:end'
    record = {'browser': {'workflow': 'rewards'}, 'receipts': [{
        'operation_id': 'old-reward', 'reward_id': 'old-unconfirmed', 'grants': [{'name': '旧奖励', 'quantity': 999}],
    }]}
    saved = []
    monkeypatch.setattr(quests, 'save', lambda: saved.append(copy.deepcopy(record)))
    async def send(message, **kwargs):
        assert saved[-1]['reward_one_notices']['moonpalace:2026-11-21:fixture-run']['state'] == 'sending'
        assert kwargs['send_as_id'] == 42 and kwargs['scope'] == 'identity'
        assert '月殿血誓' in message and '灵石×12' in message and '旧奖励' not in message
        if delivery == 'exception':
            raise RuntimeError('delivery_unknown')
        return delivery
    sender = AsyncMock(side_effect=send)
    monkeypatch.setattr(quests, 'send_audit_log', sender)
    asyncio.run(quests.notify_single_daily(42, record, 'moonpalace', proof))
    asyncio.run(quests.notify_single_daily(42, record, 'moonpalace', proof))
    sender.assert_awaited_once()
    notice = record['reward_one_notices']['moonpalace:2026-11-21:fixture-run']
    assert notice['state'] == ('sent' if delivery is True else 'unconfirmed')
    assert notice['run_id'] == 'fixture-run'
    assert 'moon-palace:fixture-run:end' in record['notice_attempted']


def test_single_daily_failure_does_not_unlock_unknown_or_reopen(monkeypatch):
    from model.features import cave_treasure_runtime as cave
    owner = SimpleNamespace(identity_id=42, account_id=42, is_current=lambda: True)
    record = {'phase': 'held', 'gameplay_uncertain': True, 'reward_batch': {'phase': 'partial'}, 'rewards_next_at': 12345}
    before = copy.deepcopy(record)
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', lambda _: owner)
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *_args, **_kw: '')
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {})
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(quests, '_entry_url', lambda: 'fixture-entry')
    launch = AsyncMock(side_effect=AssertionError('must not request a new entry'))
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', launch)
    ok, _message = asyncio.run(quests.run_once(42, reward_batch=True, daily_id='moonpalace'))
    assert not ok and record == before
    launch.assert_not_awaited()


def refresh_environment(monkeypatch, record, proof):
    from model.features import cave_treasure_runtime as cave
    owner = SimpleNamespace(identity_id=42, account_id=42, is_current=lambda: True)
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', lambda _: owner)
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *_args, **_kw: '')
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {})
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, '_entry_url', lambda: 'fixture-entry')
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, '_public_entry_allowed', lambda: True)
    monkeypatch.setattr(cave, '_parse_public_cave_entry_url', lambda _: ('token', 'url', ''))
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', AsyncMock(return_value={
        'ok': True, 'player_id': 42, 'init_data': 'fixture'}))
    monkeypatch.setattr(quests, 'build_cave_treasure_miniapp_adapter', Mock())
    monkeypatch.setattr(quests, 'build_pooled_miniapp_transport', Mock())
    monkeypatch.setattr(quests, 'build_miniapp_http_request', Mock())
    response = SimpleNamespace(ok=True, data={'ok': True, 'playerId': 42,
        'mana': {'flightLocked': False}, 'quests': {'states': {}, 'nodes': []}})
    reads = AsyncMock(return_value=response)
    monkeypatch.setattr(quests, 'run_miniapp_blocking_flow', reads)
    monkeypatch.setattr(quests, 'inspect_reward_statuses', AsyncMock(return_value=copy.deepcopy(proof['reward_one']['final'])))
    monkeypatch.setattr(quests, '_browser_evidence', lambda _: copy.deepcopy(proof))
    monkeypatch.setattr(quests, 'send_audit_log', AsyncMock(return_value=True))
    launch = AsyncMock(side_effect=AssertionError('read-only refresh must not launch'))
    monkeypatch.setattr(quests, 'run_gameplay', launch)
    return launch, reads


@pytest.mark.parametrize('mode', ['ordinary', 'batch', 'wrong_single', 'matching_single'])
def test_only_matching_single_review_can_release_held_single_without_reopening(monkeypatch, mode):
    proof = claimed_result()
    record = {'phase': 'held', 'gameplay_uncertain': True, 'browser': {
        'workflow': 'rewards', 'daily_id': 'moonpalace'}, 'reward_batch': {'phase': 'partial'}, 'rewards_next_at': 12345}
    launch, _reads = refresh_environment(monkeypatch, record, proof)
    options = {'status_only': True, 'reward_batch': mode != 'ordinary'}
    if mode in {'wrong_single', 'matching_single'}:
        options['daily_id'] = 'bloodland' if mode == 'wrong_single' else 'moonpalace'
    ok, _message = asyncio.run(quests.run_once(42, **options))
    assert record['gameplay_uncertain'] is (mode != 'matching_single')
    assert record['phase'] == ('idle' if mode == 'matching_single' else 'held')
    assert ok is (mode in {'matching_single', 'ordinary'})
    assert record['reward_batch'] == {'phase': 'partial'} and record['rewards_next_at'] == 12345
    launch.assert_not_awaited()


def test_parent_refuses_a_single_budget_crossing_server_midnight_before_entry(monkeypatch):
    from model.features import cave_treasure_runtime as cave
    # 23:50 China standard time, less than the 4640-second worst-case budget.
    monkeypatch.setattr(quests.time, 'time', lambda: 1795276200.0)
    launch = AsyncMock(side_effect=AssertionError('must refuse before the first game read'))
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', launch)
    ok, message = asyncio.run(quests.run_once(42, reward_batch=True, daily_id='moonpalace'))
    assert not ok and '零点' in message
    launch.assert_not_awaited()
