import asyncio
import json
import signal
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
                'receipts': [{'original': True}], 'run_id': 'original-run',
                'result': 'old HTTP result', 'grade': '乙', 'completed_at': 1,
                'confirmed_wave': 3, 'gains': {'旧奖励': 1}, 'cultivation': 500000,
                'report': {'old': True}}
    records = {str(identity_id): original}
    monkeypatch.setattr(browser, 'TOOL_ROOT', tmp_path)
    monkeypatch.setattr(browser, '_trial_tool_root', lambda: tmp_path)
    monkeypatch.setattr(browser.trial, '_records', lambda: records)
    monkeypatch.setattr(browser.trial, '_save', Mock())
    monkeypatch.setattr(browser.trial, '_entry_url', lambda: 'fixture-entry')
    monkeypatch.setattr(browser, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(browser, 'is_cave_public_identity_available', lambda _id: True)
    monkeypatch.setattr(browser, 'get_activity_block_reason', lambda _id, **_kwargs: '')
    monkeypatch.setattr(browser.MiniAppIdentityOwner, 'capture', lambda _id: owner)
    monkeypatch.setattr(browser, '_TASK', None)
    monkeypatch.setattr(browser, '_STATUS', {'job_id': 'fixture-job'})
    config = {'enabled': True, 'participant_identity_ids': [identity_id], 'daily_limit': 1,
              'arenas': ['longwind', 'bamboo']}
    monkeypatch.setattr(browser.trial, 'get_expedition_config', lambda: {'changfeng': config})
    notice = AsyncMock(return_value=True)
    monkeypatch.setattr(browser, 'send_audit_log', notice, raising=False)
    lock = asyncio.Lock()
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _id: lock)
    monkeypatch.setattr(cave, '_public_entry_allowed', lambda: True)
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
    async def drain(process, _token):
        if process.returncode is None:
            process.send_signal(signal.SIGTERM)
            await process.wait()
    monkeypatch.setattr(browser, '_drain_trial_process', drain)
    return SimpleNamespace(owner=owner, record=original, records=records, config=config,
                           network=network, commands=commands, launch=launch, lock=lock, notice=notice)


@pytest.fixture
def victory_network():
    # Deidentified server receipts from WA's 2026-11-16 browser run. The final
    # wave legitimately grants two separate stacks of the same potion. Tier is
    # normalized to one for the current first-tier-only execution policy.
    network = {'requests': [{'action': 'start'}], 'receipts': []}
    for wave, stones, extras in (
        (0, 105, [('回灵丹', 1)]), (1, 124, [('天雷竹', 2)]),
        (2, 124, [('天雷竹', 1)]), (3, 229, [('回灵丹', 1), ('回灵丹', 2)]),
    ):
        trial = {'run': {'id': 'browser-run', 'arena': 'longwind', 'rewarded': True, 'tier': 1, 'cleared': wave,
                         'status': 'cleared' if wave == 3 else 'active'},
                 'grants': [{'name': name, 'quantity': quantity}
                            for name, quantity in [('灵石', stones), *extras]],
                 'cultivation': 400000 if wave == 3 else 0}
        if wave == 3:
            trial.update(result={'grade': '丙', 'score': 0}, status={
                'activeRun': None, 'arena': 'longwind', 'day': browser.trial._day(browser.time.time()),
                'used': 1, 'remaining': 2, 'limit': 3, 'swordBox': 8, 'swordBoxTarget': 20,
            })
        network['receipts'].append({'ok': True, 'trial': trial})
    return network


def test_server_rewards_are_summed_once_per_wave(victory_network):
    victory_network['receipts'] += victory_network['receipts'][:]
    facts = browser.summarize_network(victory_network)
    assert facts['gains'] == {'灵石': 582, '回灵丹': 4, '天雷竹': 3}
    assert facts['cultivation'] == 400000
    assert facts['run_id'] == 'browser-run'
    assert facts['tier'] == 1
    assert facts['quota'] == {
        'arena': 'longwind', 'day': browser.trial._day(browser.time.time()),
        'used': 1, 'remaining': 2, 'limit': 3,
    }
    assert facts['sword_box'] == {'count': 8, 'target': 20}


def test_server_quota_is_retained_only_from_closed_matching_status(victory_network):
    facts = browser.summarize_network(victory_network)
    assert facts['quota'] == {
        'arena': 'longwind', 'day': browser.trial._day(browser.time.time()),
        'used': 1, 'remaining': 2, 'limit': 3,
    }
    assert facts['sword_box'] == {'count': 8, 'target': 20}
    victory_network['receipts'][-1]['trial']['status']['arena'] = 'bamboo'
    assert browser.summarize_network(victory_network)['quota'] is None


def test_trial_notification_details_find_exact_run_in_same_job(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, 'TRIAL_TOOL_ROOT', tmp_path)
    directory = tmp_path / '.runtime' / '42' / 'job_1'
    directory.mkdir(parents=True)
    (directory / 'runs.jsonl').write_text('\n'.join((
        json.dumps({'runId': 'target', 'arena': 'longwind', 'tier': 1, 'wave': 1}),
        json.dumps({'runId': 'other', 'arena': 'longwind', 'tier': 1, 'wave': 3}),
        '{broken json',
    )), encoding='utf-8')
    (directory / 'doufa-profile-longwind.json').write_text(
        json.dumps({'companion': {'name': '南宫婉'}}), encoding='utf-8')

    report, profile = browser._load_trial_notification_details({
        'identity_id': 42, 'job_id': 'job_1', 'arena': 'longwind', 'tier': 1, 'run_id': 'target',
    })

    assert report['wave'] == 1
    assert profile['companion']['name'] == '南宫婉'


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
    assert '【长风原 · 麦田试炼 · 一重】' in text and '青溪试炼' not in text
    assert '结果：通关（4/4波）；评级：丙' in text
    assert all(piece in text for piece in ('灵石×582', '回灵丹×4', '天雷竹×3', '修为：+400000'))
    assert '该场地今日剩余有奖次数：2' in text
    assert 'score' not in text and 'fixture-job' not in text and 'browser-run' not in text
    assert harness.notice.call_args.kwargs['send_as_id'] == harness.owner.identity_id
    assert harness.record['browser_trial']['gains'] == {'灵石': 582, '回灵丹': 4, '天雷竹': 3}
    assert harness.record['browser_trial_notice']['state'] == 'sent'
    asyncio.run(browser._notify_result(harness.record))
    harness.notice.assert_awaited_once()


def test_trial_notification_formats_only_verified_battle_and_reward_fields():
    status = {
        'identity_id': 42, 'arena': 'longwind', 'tier': 1, 'kind': 'victory',
        'server_closed': True, 'server_cleared': True, 'server_result': {'grade': '丙'},
        'confirmed_wave': 3, 'gains': {'灵石': 380, '天雷竹': 3, '回灵丹': 3},
        'cultivation': 4000, 'finished_at': 1795189948,
        'quota': {'arena': 'longwind', 'day': '2026-11-20', 'used': 1, 'remaining': 2, 'limit': 3},
        'sword_box': {'count': 1, 'target': 20},
    }
    run_report = {
        'runId': 'server-run', 'arena': 'longwind', 'tier': 1, 'kind': 'victory', 'wave': 3,
        'wallSeconds': 308, 'metrics': {'enemyHits': 12, 'parries': 0, 'blocks': 4, 'dodges': 7, 'potions': 3},
        'swordBox': {'count': 1, 'target': 20},
    }
    battle_profile = {
        'companion': {'name': '南宫婉', 'isNangongWan': True},
        'swords': [{'name': '玄铁剑'}],
        'slots': [{'name': '金蚨子母刃'}, {'name': '青竹蜂云剑'}],
    }

    text = browser._format_trial_notification(
        status, run_report, battle_profile, {'username': 'LongWeikaishui', 'label': 'ignored'},
    )

    assert '【长风原 · 麦田试炼 · 一重】 @LongWeikaishui' in text
    assert '驭侍妾南宫婉出战。' in text
    assert '通关（4/4波）；评级：丙' in text
    assert '战报用时：5分8秒 · 受击 12 · 精准格挡 0 · 格挡 4 · 翻滚 7 · 用药 3' in text
    assert '飞剑：玄铁剑；法宝：金蚨子母刃、青竹蜂云剑' in text
    assert '奖励：灵石×380、天雷竹×3、回灵丹×3（剑匣 1/20）' in text
    assert '修为：+4000' in text
    assert '该场地今日剩余有奖次数：2' in text
    assert '结算时间：2026-11-20 23:52:28 北京时间' in text
    assert 'server-run' not in text


def test_trial_notification_omits_unobserved_metrics_equipment_and_quota():
    status = {
        'identity_id': 42, 'arena': 'bamboo', 'tier': 2, 'kind': 'defeat',
        'server_closed': True, 'server_cleared': False, 'rewarded': True, 'server_result': None,
        'confirmed_wave': 0, 'gains': {}, 'cultivation': 0, 'quota': None,
    }

    text = browser._format_trial_notification(
        status, {}, {}, {'username': '', 'label': 'Test identity'},
    )

    assert '【长风原 · 竹林试炼 · 二重】 Test identity' in text
    assert '战败（1/4波）；评级：无' in text
    assert '奖励：无确认物品' in text and '修为：+0' in text
    assert '精准格挡' not in text
    assert '法宝：' not in text and '飞剑：' not in text
    assert '剩余有奖次数' not in text


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
    assert '结果：通关' not in text and '结果：未通关（3/4波）' in text
    assert '灵石×353' in text and '回灵丹×1' in text and '天雷竹×3' in text
    assert '修为+' not in text


def test_scheduled_browser_completion_replaces_old_http_result(harness, victory_network):
    harness.record['day'] = '2000-01-01'
    harness.network.update(victory_network)

    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job',
                             scheduled_config=harness.config))

    assert harness.record['phase'] == 'done'
    assert harness.record['run_id'] == 'browser-run'
    assert harness.record['previous_run_id'] == 'original-run'
    assert harness.record['previous_attempt']['result'] == 'old HTTP result'
    assert harness.record['previous_attempt']['receipts'] == [{'original': True}]
    assert harness.record['result'].startswith('已通关｜')
    assert '旧奖励' not in harness.record['result']
    assert harness.record['grade'] == '丙'
    assert harness.record['gains'] == {'灵石': 582, '回灵丹': 4, '天雷竹': 3}
    assert harness.record['cultivation'] == 400000
    assert harness.record['confirmed_wave'] == 3
    assert harness.record['completed_at'] == browser.snapshot()['finished_at']


def test_scheduled_partial_close_updates_result_without_stale_grade(harness, victory_network):
    harness.record['day'] = '2000-01-01'
    harness.network.update(victory_network)
    harness.network['receipts'] = victory_network['receipts'][:3] + [
        {'ok': True, 'trial': {'status': {'activeRun': None}}},
    ]

    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job',
                             scheduled_config=harness.config))

    assert harness.record['phase'] == 'done'
    assert harness.record['result'].startswith('已结束｜')
    assert '灵石×353' in harness.record['result']
    assert harness.record['gains'] == {'灵石': 353, '回灵丹': 1, '天雷竹': 3}
    assert harness.record['cultivation'] == 0
    assert 'grade' not in harness.record
    assert harness.record['confirmed_wave'] == 2
    assert harness.record['previous_attempt']['grade'] == '乙'


def test_scheduled_unknown_run_holds_without_old_completion(harness):
    harness.record['day'] = '2000-01-01'
    harness.network['requests'] = [{'action': 'start'}]

    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job',
                             scheduled_config=harness.config))

    assert harness.record['phase'] == 'held'
    assert 'result' not in harness.record
    assert 'grade' not in harness.record
    assert 'completed_at' not in harness.record
    assert harness.record['previous_attempt']['result'] == 'old HTTP result'
    assert harness.record['previous_run_id'] == 'original-run'


def test_startup_reconciles_legacy_browser_result_once(harness, monkeypatch):
    dirty = Mock()
    monkeypatch.setattr(browser.trial, 'mark_dirty', dirty)
    harness.record.update(
        phase='done',
        completed_at=100,
        run_id='browser-run',
        previous_run_id='http-run',
        operation_id='old-http-operation',
        browser_trial_notice={'run_id': 'browser-run', 'state': 'sent'},
        browser_trial={
            'arena': 'longwind', 'run_id': 'browser-run', 'job_id': 'browser-job',
            'scheduled': True, 'rewarded': True,
            'server_closed': True, 'server_cleared': False,
            'gains': {'灵石': 8}, 'cultivation': 0, 'tier': 2,
            'finished_at': 200,
        },
    )

    browser.trial.reset_changfeng_runtime_on_startup()
    assert harness.record['result'] == '已结束｜灵石×8'
    assert 'grade' not in harness.record
    assert harness.record['completed_at'] == 200
    assert harness.record['previous_attempt']['result'] == 'old HTTP result'
    assert 'browser_trial' not in harness.record['previous_attempt']
    assert harness.record['browser_trial_notice']['run_id'] == 'browser-run'
    assert harness.record['previous_run_id'] == 'http-run'
    assert harness.record['operation_id'] == 'browser-browser-job'
    harness.record['browser_trial'].pop('finished_at')
    browser.trial.reset_changfeng_runtime_on_startup()
    dirty.assert_called_once()


@pytest.mark.parametrize('finished_at', [None, 200])
@pytest.mark.parametrize('completed_at', [None, 100])
def test_startup_repairs_current_result_metadata_without_rearchiving(harness, monkeypatch, finished_at, completed_at):
    import copy

    dirty = Mock()
    monkeypatch.setattr(browser.trial, 'mark_dirty', dirty)
    harness.record.update(
        run_id='browser-run', operation_id='legacy-http-operation', result='已结束｜灵石×8',
        previous_attempt={'run_id': 'http-run', 'result': 'HTTP previous result'},
        browser_trial_notice={'run_id': 'browser-run', 'state': 'sent'},
        browser_trial={'run_id': 'browser-run', 'job_id': 'browser-job', 'scheduled': True,
                       'arena': 'longwind', 'rewarded': True, 'server_closed': True,
                       'gains': {'灵石': 8}},
    )
    if completed_at is not None:
        harness.record['completed_at'] = completed_at
    if finished_at is not None:
        harness.record['browser_trial']['finished_at'] = finished_at
    previous = copy.deepcopy(harness.record['previous_attempt'])
    notice = copy.deepcopy(harness.record['browser_trial_notice'])

    browser.trial.reset_changfeng_runtime_on_startup()
    assert harness.record['operation_id'] == 'browser-browser-job'
    assert harness.record['execution_engine'] == 'browser-m19'
    assert harness.record['previous_attempt'] == previous
    assert harness.record['browser_trial_notice'] == notice
    if finished_at is not None or completed_at is not None:
        assert harness.record['completed_at'] == (finished_at or completed_at)
    after = copy.deepcopy(harness.record)
    browser.trial.reset_changfeng_runtime_on_startup()
    assert harness.record == after
    dirty.assert_called_once()
    harness.notice.assert_not_awaited()


def test_local_victory_without_server_rewards_never_notifies(harness):
    harness.network['requests'] = [{'action': 'start'}]
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'fixture-job'))
    harness.notice.assert_not_awaited()


def test_confirmed_zero_reward_defeat_notifies_once_for_exact_job(harness):
    harness.record['browser_trial'] = {
        'job_id': 'defeat-job', 'identity_id': harness.owner.identity_id, 'arena': 'longwind', 'tier': 1,
        'kind': 'defeat', 'server_closed': True, 'server_cleared': False, 'rewarded': True,
        'run_id': 'defeated-run', 'gains': {}, 'cultivation': 0,
    }
    payload = {'action': 'notify', 'send_as_id': harness.owner.identity_id, 'job_id': 'defeat-job'}
    ok, _message, result = asyncio.run(browser.action({**payload, 'job_id': 'other'}))
    assert not ok
    harness.notice.assert_not_awaited()
    ok, _message, result = asyncio.run(browser.action(payload))
    assert ok and result['browser_trial']['notice']['state'] == 'sent'
    text = harness.notice.call_args.args[0]
    assert '结果：战败（波次待核）；评级：无' in text and '奖励：无确认物品' in text
    assert '已确认收获' not in text and 'defeated-run' not in text
    asyncio.run(browser.action(payload))
    harness.notice.assert_awaited_once()


def test_confirmed_defeat_pause_survives_restart_without_replaying_notice(harness, monkeypatch):
    harness.record['daily_used'] = 0
    harness.record['browser_trial'] = {'job_id': 'defeat-job', 'identity_id': harness.owner.identity_id,
        'arena': 'longwind', 'run_id': 'defeated-run', 'kind': 'defeat', 'rewarded': True,
        'server_closed': True, 'server_cleared': False, 'finished_at': 100, 'tier': 1}
    dirty = Mock()
    monkeypatch.setattr(browser.trial, 'mark_dirty', dirty)
    browser.trial.reset_changfeng_runtime_on_startup()
    assert harness.record['phase'] == 'done'
    assert harness.record['auto_pause']['run_id'] == 'defeated-run'
    browser.trial.reset_changfeng_runtime_on_startup()
    dirty.assert_called_once()
    harness.notice.assert_not_awaited()
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert not harness.commands


def test_only_verified_manual_win_clears_defeat_pause(harness, victory_network):
    harness.record['auto_pause'] = {'reason': 'confirmed_defeat', 'run_id': 'previous-loss'}
    harness.network.update(victory_network)
    asyncio.run(browser._run(harness.owner, 'longwind', False, 'manual-retest'))
    assert 'auto_pause' not in harness.record


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


def test_server_tier_mismatch_stays_held_without_publishing_completion(harness, victory_network):
    harness.record['daily_used'] = 0
    harness.network.update(victory_network)
    for row in harness.network['receipts']:
        row['trial']['run']['tier'] = 2
    ok, _message = asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))
    assert not ok
    assert harness.record['phase'] == 'held'
    assert harness.record['last_error'] == '浏览器试炼回执重数不符，结果待核对'
    assert harness.record['daily_used'] == 1
    assert harness.record['browser_trial']['tier'] == 2
    assert 'completed_at' not in harness.record
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert len(harness.commands) == 1


def test_no_game_request_releases_ownership(harness):
    harness.network.update(requests=[{'action': 'status'}], receipts=[
        {'ok': True, 'trial': {'status': {'activeRun': None}}},
    ])
    asyncio.run(browser._run(harness.owner, 'bamboo', True, 'fixture-job'))
    assert harness.record['phase'] == 'done'
    assert harness.record['daily_used'] == 1
    assert '--practice-only' in harness.commands[0]


def test_cleanup_failure_holds_completed_trial_and_removes_launch_secret(harness, victory_network, monkeypatch):
    harness.record['daily_used'] = 0
    harness.network.update(victory_network)
    monkeypatch.setattr(browser, '_drain_trial_process', AsyncMock(side_effect=RuntimeError('fixture cleanup failure')))
    with pytest.raises(RuntimeError, match='cleanup failure'):
        asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))
    assert harness.record['phase'] == 'held'
    assert harness.record['browser_trial']['cleanup_error'] == 'RuntimeError'
    assert browser._TASK is None and not harness.lock.locked()
    command = harness.commands[0]
    assert not Path(command[command.index('--url-file') + 1]).exists()


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


@pytest.mark.parametrize('taint', ['empty', 'unknown', 'active_run', 'failed_status', 'terminal_unknown'])
def test_incomplete_or_unknown_journal_never_proves_no_start(harness, victory_network, taint):
    harness.record['daily_used'] = 0
    if taint == 'unknown':
        harness.network['requests'] = [{'action': 'unknown'}]
    elif taint == 'active_run':
        harness.network['receipts'] = [{'ok': True, 'trial': {
            'run': {'id': 'capture-gap', 'status': 'active', 'rewarded': True},
        }}]
    elif taint == 'failed_status':
        harness.network.update(requests=[{'action': 'status'}], receipts=[
            {'ok': False, 'error': 'receipt_capture_failed'},
            {'ok': True, 'trial': {'status': {'activeRun': None}}},
        ])
    elif taint == 'terminal_unknown':
        harness.network.update(victory_network)
        harness.network['requests'].append({'action': 'unknown'})
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert harness.record['phase'] == 'held'
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert len(harness.commands) == 1


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


@pytest.mark.parametrize('tier', [None, 0, 2, 30, 31, True, '1', 1.0])
def test_manual_action_rejects_invalid_tier_before_browser_launch(harness, tier):
    ok, message, _extra = asyncio.run(browser.action({
        'action': 'start', 'send_as_id': harness.owner.identity_id, 'tier': tier,
    }))
    assert not ok and message == '当前仅验证第一重，重数须为整数1'
    assert not harness.commands


def test_manual_action_pins_explicit_first_tier(harness, victory_network):
    harness.record['daily_used'] = 0
    harness.network.update(victory_network)

    async def start():
        ok, _message, _extra = await browser.action({
            'action': 'start', 'send_as_id': harness.owner.identity_id, 'tier': 1,
        })
        assert ok
        await browser._TASK

    asyncio.run(start())
    command = harness.commands[0]
    assert command[command.index('--tier') + 1] == '1'
    assert command[command.index('--retries') + 1] == '0'
    assert harness.record['browser_trial']['tier'] == 1


def test_snapshot_does_not_return_login_url(harness):
    ok, _message, extra = asyncio.run(browser.action({'action': 'status'}))
    assert ok
    assert 'tgWebAppData' not in json.dumps(extra)


def test_unverified_client_victory_is_not_server_completion():
    facts = browser.summarize_network({'requests': [{'action': 'start'}], 'receipts': []})
    assert {key: facts[key] for key in ('started', 'rewarded', 'terminal', 'closed')} == {
        'started': True, 'rewarded': False, 'terminal': None, 'closed': False}


def test_scheduled_browser_uses_native_m19_and_stops_at_local_daily_limit(harness, victory_network, monkeypatch):
    harness.record['daily_used'] = 0
    harness.network.update(victory_network)
    synthetic = Mock(side_effect=AssertionError('scheduled browser must never synthesize a report'))
    monkeypatch.setattr(browser.trial, '_trial_report', synthetic)
    ok, _message = asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))
    assert ok
    assert harness.record['daily_used'] == 1
    assert harness.record['execution_engine'] == 'browser-m19'
    assert harness.record['run_id'] == 'browser-run'
    assert harness.record['previous_run_id'] == 'original-run'
    command = harness.commands[0]
    assert Path(command[1]).name == 'changfeng_trial_browser.py'
    assert command[command.index('--runs') + 1] == '1'
    assert command[command.index('--tier') + 1] == '1'
    assert command[command.index('--retries') + 1] == '0'
    assert '--practice-only' not in command and '--practice-ok' not in command
    assert browser._TASK is None
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert len(harness.commands) == 1
    synthetic.assert_not_called()


def test_scheduler_routes_to_browser_and_never_to_legacy_http(harness, victory_network, monkeypatch):
    trial = browser.trial
    harness.record['daily_used'] = 0
    harness.network.update(victory_network)
    legacy = AsyncMock(side_effect=AssertionError('legacy HTTP route must not run'))
    monkeypatch.setattr(trial, 'run_changfeng_once', legacy)
    monkeypatch.setattr(trial, 'get_identity_ids', lambda: [harness.owner.identity_id])
    monkeypatch.setattr(trial, 'is_cave_public_identity_available', lambda _id: True)
    monkeypatch.setattr(trial, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(trial, 'get_activity_block_reason', lambda *_args, **_kwargs: '')
    monkeypatch.setattr(trial, '_TASKS', {})

    async def run():
        await trial.run_changfeng_scheduler(browser.time.time())
        await asyncio.gather(*list(trial._TASKS.values()))
        await trial.run_changfeng_scheduler(browser.time.time())

    asyncio.run(run())
    assert len(harness.commands) == 1
    assert harness.record['daily_used'] == 1
    legacy.assert_not_awaited()


@pytest.mark.parametrize('blocked', ['held', 'limit', 'busy', 'disabled', 'foreign_owner', 'entry_lock', 'future'])
def test_scheduled_browser_rejects_before_launch(harness, monkeypatch, blocked):
    harness.record['daily_used'] = 0
    if blocked == 'held':
        harness.record['phase'] = 'held'
    elif blocked == 'limit':
        harness.record['daily_used'] = 1
    elif blocked == 'busy':
        monkeypatch.setattr(browser, 'get_activity_block_reason', lambda *_a, **_k: '青溪旧局待核对')
    elif blocked == 'disabled':
        harness.config['enabled'] = False
    elif blocked == 'foreign_owner':
        harness.owner.account_id += 1
    elif blocked == 'future':
        harness.record['next_at'] = browser.time.time() + 1800

    async def run():
        if blocked == 'entry_lock':
            await harness.lock.acquire()
        try:
            assert not (await browser.run_scheduled_once(harness.owner.identity_id))[0]
        finally:
            if harness.lock.locked():
                harness.lock.release()

    asyncio.run(run())
    assert not harness.commands
    harness.launch.assert_not_awaited()


def test_real_exhausted_status_without_start_defers_to_next_day(harness):
    harness.record['daily_used'] = 0
    harness.network.update(requests=[{'action': 'status'}], receipts=[{
        'ok': True, 'trial': {'status': {'remaining': 0, 'used': 3, 'limit': 3, 'activeRun': None}},
    }])
    assert asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert harness.record['phase'] == 'done'
    assert harness.record['daily_used'] == 0
    assert harness.record['exhausted'] is True
    assert harness.record['next_at'] > browser.time.time() + 3600
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert len(harness.commands) == 1
    harness.notice.assert_not_awaited()


def test_no_reward_start_closed_natively_never_reopens_same_day(harness):
    harness.record['daily_used'] = 0
    harness.network.update(requests=[{'action': 'start'}, {'action': 'end'}], receipts=[
        {'ok': True, 'trial': {'run': {'id': 'practice-run', 'rewarded': False}}},
        {'ok': True, 'trial': {'status': {'activeRun': None}}},
    ])
    assert asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert harness.record['daily_used'] == 0
    assert harness.record['exhausted'] is True
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert len(harness.commands) == 1
    harness.notice.assert_not_awaited()


@pytest.mark.parametrize('problem', ['unknown_start', 'unknown_reward', 'wrong_player', 'cancel', 'save_before_launch'])
def test_scheduled_browser_uncertain_outcomes_remain_held_without_replay(harness, monkeypatch, problem):
    harness.record['daily_used'] = 0
    harness.network['requests'] = [{'action': 'start'}]
    if problem == 'unknown_reward':
        harness.network['receipts'] = [
            {'ok': True, 'trial': {'run': {'id': 'unknown-run'}}},
            {'ok': True, 'trial': {'status': {'activeRun': None}}},
        ]
    elif problem == 'wrong_player':
        harness.network['receipts'] = [{'ok': True, 'playerId': 999, 'trial': {
            'run': {'id': 'wrong-player', 'rewarded': True}, 'result': {'grade': '甲'},
        }}]
    elif problem == 'cancel':
        async def cancelled(*_args, **_kwargs):
            raise asyncio.CancelledError()
        monkeypatch.setattr(browser.asyncio, 'create_subprocess_exec', cancelled)
    elif problem == 'save_before_launch':
        calls = [0]
        def save():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('disk unavailable')
        monkeypatch.setattr(browser.trial, '_save', save)

    async def run():
        if problem == 'cancel':
            with pytest.raises(asyncio.CancelledError):
                await browser.run_scheduled_once(harness.owner.identity_id)
        else:
            assert not (await browser.run_scheduled_once(harness.owner.identity_id))[0]

    asyncio.run(run())
    if problem != 'save_before_launch':
        assert harness.record['phase'] == 'held'
        assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert harness.record['daily_used'] == 0
    assert browser._TASK is None and not harness.lock.locked()
    if problem == 'save_before_launch':
        assert not harness.commands
        harness.launch.assert_not_awaited()


def test_trial_restart_marks_browser_record_held_before_scheduling(harness, monkeypatch):
    harness.record.update(phase='browser_trial', operation_id='browser-running', daily_used=0)
    monkeypatch.setattr(browser.trial, 'mark_dirty', Mock())
    browser.trial.reset_changfeng_runtime_on_startup()
    assert harness.record['phase'] == 'held'
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert not harness.commands


def test_trial_root_is_pinned_and_does_not_replace_npc_helper_root(tmp_path, monkeypatch):
    root = tmp_path / 'm19'
    for part in ('.venv/bin/python', 'tools/longwind_trial_bot.py', 'tools/longwind_trial_bot.js'):
        path = root / part
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
    npc_root = browser.TOOL_ROOT
    monkeypatch.setattr(browser, 'TRIAL_TOOL_ROOT', root)
    with pytest.raises(RuntimeError, match='strategy_changed'):
        browser._trial_tool_root()
    assert browser.TOOL_ROOT == npc_root


def test_scheduled_browser_config_change_aborts_before_process_launch(harness):
    harness.record['daily_used'] = 0
    launch_url = harness.launch.return_value
    async def changed(*_args, **_kwargs):
        harness.config['enabled'] = False
        return launch_url
    harness.launch.side_effect = changed
    assert not asyncio.run(browser.run_scheduled_once(harness.owner.identity_id))[0]
    assert not harness.commands
    assert harness.record['phase'] == 'done'
    assert browser._TASK is None and not harness.lock.locked()


def test_cancelling_spawn_recovers_and_stops_own_child_before_holding(harness, monkeypatch):
    harness.record['daily_used'] = 0
    async def run():
        spawned, stopped = asyncio.Event(), asyncio.Event()
        class Child:
            returncode = None
            def send_signal(self, _signal):
                self.returncode = 130
                stopped.set()
            async def wait(self):
                await stopped.wait()
                return self.returncode
        child = Child()
        async def spawn(*_args, **_kwargs):
            spawned.set()
            return child
        monkeypatch.setattr(browser.asyncio, 'create_subprocess_exec', spawn)
        task = asyncio.create_task(browser.run_scheduled_once(harness.owner.identity_id))
        await spawned.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
    asyncio.run(run())
    assert harness.record['phase'] == 'held'
    assert browser._TASK is None and not harness.lock.locked()
