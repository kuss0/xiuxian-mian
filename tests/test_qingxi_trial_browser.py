import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest

from model.features import qingxi_trial_browser as browser
from model.features import cave_treasure_runtime as cave


@pytest.fixture
def harness(monkeypatch, tmp_path):
    identity_id = 301299112
    owner = SimpleNamespace(identity_id=identity_id, account_id=identity_id, is_current=lambda: True)
    original = {'phase': 'done', 'day': browser.trial._day(browser.time.time()), 'daily_used': 1,
                'receipts': [{'original': True}], 'run_id': 'original-run'}
    records = {str(identity_id): original}
    monkeypatch.setattr(browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(browser.trial, '_records', lambda: records)
    monkeypatch.setattr(browser.trial, '_save', Mock())
    monkeypatch.setattr(browser.trial, '_entry_url', lambda: 'fixture-entry')
    monkeypatch.setattr(browser, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(browser, 'is_cave_public_identity_available', lambda _id: True)
    monkeypatch.setattr(browser, 'get_activity_block_reason', lambda _id: '')
    monkeypatch.setattr(browser.MiniAppIdentityOwner, 'capture', lambda _id: owner)
    monkeypatch.setattr(browser, '_TASK', None)
    monkeypatch.setattr(browser, '_STATUS', {'job_id': 'fixture-job'})
    notice = AsyncMock(return_value=True)
    monkeypatch.setattr(browser, 'send_audit_log', notice, raising=False)
    lock = asyncio.Lock()
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _id: lock)
    monkeypatch.setattr(cave, '_parse_public_cave_entry_url', lambda _url: ('fixture-token', 'fixture-url', ''))
    init_data = urlencode({'user': json.dumps({'id': identity_id}), 'auth_date': '1', 'hash': 'fixture'})
    url = 'https://asc.aiopenai.app/miniapp/xianxia-dwelling#' + urlencode({'tgWebAppData': init_data})
    launch = AsyncMock(return_value=url)
    monkeypatch.setattr(browser, 'request_cave_treasure_miniapp_init_data', launch)
    network = {'requests': [], 'receipts': []}
    commands = []

    async def spawn(*command, **_kwargs):
        commands.append(command)
        path = Path(command[command.index('--log') + 1])
        path.with_suffix('.network.json').write_text(json.dumps(network))
        return SimpleNamespace(returncode=0, wait=AsyncMock(return_value=0))

    monkeypatch.setattr(browser.asyncio, 'create_subprocess_exec', spawn)
    return SimpleNamespace(owner=owner, record=original, network=network, commands=commands,
                           launch=launch, lock=lock, notice=notice)


@pytest.fixture
def victory_network():
    # Deidentified server receipts from WA's 2026-11-16 browser run. The final
    # wave legitimately grants two separate stacks of the same potion.
    network = {'requests': [{'action': 'start'}], 'receipts': []}
    for wave, stones, extras in (
        (0, 105, [('回灵丹', 1)]), (1, 124, [('天雷竹', 2)]),
        (2, 124, [('天雷竹', 1)]), (3, 229, [('回灵丹', 1), ('回灵丹', 2)]),
    ):
        trial = {'run': {'id': 'browser-run', 'rewarded': True, 'tier': 2, 'cleared': wave,
                         'status': 'cleared' if wave == 3 else 'active'},
                 'grants': [{'name': name, 'quantity': quantity}
                            for name, quantity in [('灵石', stones), *extras]],
                 'cultivation': 400000 if wave == 3 else 0}
        if wave == 3:
            trial.update(result={'grade': '丙', 'score': 0}, status={'activeRun': None})
        network['receipts'].append({'ok': True, 'trial': trial})
    return network


def test_server_rewards_are_summed_once_per_wave(victory_network):
    victory_network['receipts'] += victory_network['receipts'][:]
    facts = browser.summarize_network(victory_network)
    assert facts['gains'] == {'灵石': 582, '回灵丹': 4, '天雷竹': 3}
    assert facts['cultivation'] == 400000
    assert facts['run_id'] == 'browser-run'
    assert facts['tier'] == 2


def test_rewards_ignore_failed_pending_anomalous_and_other_run_receipts(victory_network):
    import copy

    noise = []
    for flags in ({'ok': False}, {'pending': True}, {'anomaly': 'unconfirmed'}, {'other_run': True}):
        row = copy.deepcopy(victory_network['receipts'][0])
        row['trial']['grants'] = [{'name': '伪奖励', 'quantity': 999}]
        row['trial']['cultivation'] = 999
        if 'ok' in flags:
            row['ok'] = flags['ok']
        elif 'other_run' in flags:
            row['trial']['run']['id'] = 'old-run'
        else:
            row['trial'].update(flags)
        noise.append(row)
    victory_network['receipts'] = noise + victory_network['receipts']
    facts = browser.summarize_network(victory_network)
    assert facts['gains'] == {'灵石': 582, '回灵丹': 4, '天雷竹': 3}
    assert facts['cultivation'] == 400000


def test_browser_completion_notifies_original_identity_once(harness, victory_network):
    harness.network.update(victory_network)
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    text = harness.notice.call_args.args[0]
    assert '青溪试炼·麦田试炼' in text and '第2重' in text and '通关' in text
    assert all(piece in text for piece in ('灵石×582', '回灵丹×4', '天雷竹×3', '修为+400000'))
    assert 'score' not in text and 'fixture-job' not in text and 'browser-run' not in text
    assert harness.notice.call_args.kwargs['send_as_id'] == harness.owner.identity_id
    assert harness.record['browser_trial']['gains'] == {'灵石': 582, '回灵丹': 4, '天雷竹': 3}
    assert harness.record['browser_trial_notice']['state'] == 'sent'
    asyncio.run(browser._notify_result(harness.record))
    harness.notice.assert_awaited_once()


@pytest.mark.parametrize('raises', [False, True])
def test_notification_failure_preserves_completion_and_never_replays(harness, victory_network, raises):
    harness.network.update(victory_network)
    if raises:
        harness.notice.side_effect = RuntimeError('transport_failed')
    else:
        harness.notice.return_value = False
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    assert harness.record['phase'] == 'done'
    assert harness.record['daily_used'] == 2
    assert browser.snapshot()['phase'] == 'done'
    assert harness.record['browser_trial_notice']['state'] == 'unconfirmed'
    asyncio.run(browser._notify_result(harness.record))
    harness.notice.assert_awaited_once()


def test_closed_timeout_reports_only_confirmed_partial_rewards(harness, victory_network):
    harness.network.update(victory_network)
    harness.network['receipts'] = victory_network['receipts'][:3] + [
        {'ok': True, 'trial': {'status': {'activeRun': None}}},
    ]
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    text = harness.notice.call_args.args[0]
    assert '通关' not in text and '已结束' in text
    assert '灵石×353' in text and '回灵丹×1' in text and '天雷竹×3' in text
    assert '修为+' not in text


def test_local_victory_without_server_rewards_never_notifies(harness):
    harness.network['requests'] = [{'action': 'start'}]
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    harness.notice.assert_not_awaited()


def test_terminal_server_receipt_preserves_original_and_counts_one_extra(harness):
    h = harness
    h.network.update(requests=[{'action': 'start'}], receipts=[
        {'ok': True, 'trial': {'run': {'rewarded': True}}},
        {'ok': True, 'trial': {'result': {'grade': '甲', 'score': 77}, 'grants': []}},
    ])
    asyncio.run(browser._run(h.owner, 'longwind', False, 'fixture-job'))
    assert h.record['phase'] == 'done'
    assert h.record['daily_used'] == 2
    assert h.record['receipts'] == [{'original': True}]
    assert h.record['run_id'] == 'original-run'
    command = h.commands[0]
    assert command[command.index('--runs') + 1] == '1'
    assert command[command.index('--expected-player-id') + 1] == str(h.owner.identity_id)
    assert 'tgWebAppData' not in ' '.join(command)
    assert not Path(command[command.index('--url-file') + 1]).exists()
    assert not h.lock.locked()


def test_no_game_request_releases_ownership(harness):
    asyncio.run(browser._run(harness.owner, 'bamboo', True, 'fixture-job'))
    assert harness.record['phase'] == 'done'
    assert harness.record['daily_used'] == 1
    assert '--practice-only' in harness.commands[0]


def test_aborted_run_closed_by_server_releases_without_claiming_victory(harness):
    harness.network.update(requests=[{'action': 'start'}, {'action': 'end'}], receipts=[
        {'ok': True, 'trial': {'run': {'id': 'browser-run', 'rewarded': True}}},
        {'ok': True, 'trial': {'status': {'activeRun': None, 'remaining': 2}}},
    ])
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    assert harness.record['phase'] == 'done'
    assert harness.record['daily_used'] == 2
    assert browser.snapshot()['phase'] == 'stopped'
    assert browser.snapshot()['server_closed'] is True
    assert browser.snapshot()['server_result'] is None


def test_prestart_idle_status_does_not_release_ambiguous_start(harness):
    harness.network.update(requests=[{'action': 'status'}, {'action': 'start'}], receipts=[
        {'ok': True, 'trial': {'status': {'activeRun': None}}},
    ])
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    assert harness.record['phase'] == 'held'
    assert browser.snapshot()['server_closed'] is False


def test_unknown_started_result_is_held(harness):
    harness.network['requests'] = [{'action': 'start'}]
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    assert harness.record['phase'] == 'held'
    assert harness.record['daily_used'] == 1


def test_wrong_account_never_launches_browser(harness):
    harness.launch.return_value = 'https://asc.aiopenai.app/miniapp/xianxia-dwelling#' + urlencode({
        'tgWebAppData': urlencode({'user': json.dumps({'id': 999}), 'hash': 'fixture'})})
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    assert not harness.commands
    assert harness.record['phase'] == 'done'
    assert 'tgWebAppData' not in json.dumps(browser.snapshot())


def test_busy_identity_is_not_touched(harness, monkeypatch):
    monkeypatch.setattr(browser, 'get_activity_block_reason', lambda _id: '副本执行中')
    ok, message, _extra = asyncio.run(browser.action({'action': 'start', 'send_as_id': harness.owner.identity_id}))
    assert not ok and message == '副本执行中'
    assert not harness.commands


def test_global_pause_is_respected(harness, monkeypatch):
    monkeypatch.setattr(browser, 'get_global_enabled', lambda: False)
    ok, _message, _extra = asyncio.run(browser.action({'action': 'start', 'send_as_id': harness.owner.identity_id}))
    assert not ok
    assert not harness.commands


def test_invalid_arena_is_rejected(harness):
    ok, _message, _extra = asyncio.run(browser.action({'action': 'start', 'send_as_id': harness.owner.identity_id, 'arena': '../bad'}))
    assert not ok


def test_snapshot_does_not_return_login_url(harness):
    ok, _message, extra = asyncio.run(browser.action({'action': 'status'}))
    assert ok
    assert 'tgWebAppData' not in json.dumps(extra)


def test_unverified_client_victory_is_not_server_completion():
    facts = browser.summarize_network({'requests': [{'action': 'start'}], 'receipts': []})
    assert {key: facts[key] for key in ('started', 'rewarded', 'terminal', 'closed')} == {
        'started': True, 'rewarded': False, 'terminal': None, 'closed': False}
