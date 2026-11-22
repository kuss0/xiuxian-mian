import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import cave_treasure_runtime as cave
from model.features import qingxi_quests as quests
from model import activity_occupancy


def response(run):
    return {'ok': True, 'bloodLand': {'blood': {'day': '2026-11-22',
        'claimed': False, 'hasKey': True, 'run': run}}}


def test_active_run_keeps_bounded_owned_evidence_without_becoming_success():
    async def read(action, **kwargs):
        assert action == 'status'
        if kwargs['_api_path'].endswith('moon-palace'):
            return {'ok': True, 'moonPalace': {'moon': {'day': '2026-11-22',
                'claimed': False, 'hasKey': True, 'run': None}}}
        return response({'id': 'fixture-run', 'rewarded': True, 'created_at': 1700000000,
            'seed': 'fixture_secret', 'token': 'fixture_secret'})
    with pytest.raises(quests.RewardStatusNeedsReview) as caught:
        asyncio.run(quests.inspect_reward_statuses(read, 42))
    evidence = caught.value.evidence
    assert evidence['verified'] and evidence['run_id'] == 'fixture-run'
    assert evidence['player'] == 42 and evidence['daily_id'] == 'bloodland'
    assert not evidence['no_active_run']
    assert 'fixture_secret' not in str(evidence)


def setup_owner(monkeypatch, record):
    owner = SimpleNamespace(identity_id=42, account_id=42, is_current=lambda: True)
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', lambda _: owner)
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *_args, **_kwargs: '')
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {})
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, '_entry_url', lambda: 'https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999')
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, '_public_entry_allowed', lambda: True)
    loader = AsyncMock(return_value={'ok': True, 'player_id': 42, 'init_data': 'fixture'})
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', loader)
    monkeypatch.setattr(quests, 'build_cave_treasure_miniapp_adapter', Mock())
    monkeypatch.setattr(quests, 'build_pooled_miniapp_transport', Mock())
    monkeypatch.setattr(quests, 'save', Mock())
    return loader


def test_verified_active_status_holds_the_account_and_preserves_old_results(monkeypatch):
    record = {'phase': 'waiting_gameplay', 'browser': {'workflow': 'quests'},
              'reward_inspection': {'checked_at': 123}, 'receipts': [{'operation_id': 'original'}]}
    setup_owner(monkeypatch, record)
    evidence = quests.instance_status_evidence(response({'id': 'fixture-run'}), quests.REWARD_RESOURCE_SPECS[1], 42)
    monkeypatch.setattr(quests, 'inspect_reward_statuses', AsyncMock(side_effect=quests.RewardStatusNeedsReview(evidence)))
    ok, _ = asyncio.run(quests.run_once(42, status_only=True, reward_batch=True, daily_id='bloodland'))
    assert not ok and record['phase'] == 'held' and record['gameplay_uncertain'] is True
    assert record['pending_instance']['run_id'] == 'fixture-run'
    assert record['reward_inspection'] == {'checked_at': 123}
    assert record['receipts'] == [{'operation_id': 'original'}]


@pytest.mark.parametrize('daily_id,player', [('moonpalace', 42), ('bloodland', 43)])
def test_review_evidence_never_opens_another_scope(daily_id, player):
    record = {'pending_instance': quests.instance_status_evidence(
        response({'id': 'fixture-run'}), quests.REWARD_RESOURCE_SPECS[1], 42)}
    assert not quests.same_instance_read_review(record, player, daily_id)


def test_known_instance_blocks_other_actions_even_if_the_old_flag_was_cleared(monkeypatch):
    pending = quests.instance_status_evidence(response({'id': 'fixture-run'}), quests.REWARD_RESOURCE_SPECS[1], 42)
    record = {'phase': 'idle', 'gameplay_uncertain': False, 'pending_instance': pending}
    loader = setup_owner(monkeypatch, record)
    for options in ({}, {'status_only': True}, {'reward_batch': True, 'daily_id': 'moonpalace'}):
        ok, _message = asyncio.run(quests.run_once(42, **options))
        assert not ok
    loader.assert_not_awaited()
    monkeypatch.setattr(activity_occupancy, 'get_expedition_run_state', lambda: {'qingxi_quests': {'42': record}})
    monkeypatch.setattr(activity_occupancy, 'get_hunt_run_state', lambda: {})
    monkeypatch.setattr(activity_occupancy, 'get_replica_run_state', lambda: {})
    assert activity_occupancy.get_activity_block_reason(42)
    assert not activity_occupancy.get_activity_block_reason(42, include_qingxi_review=False)


@pytest.mark.parametrize('run', [{'id': 'bad/id'}, {}, 'unknown'])
def test_incomplete_active_run_holds_without_allowing_scoped_release(monkeypatch, run):
    record = {'phase': 'waiting_gameplay', 'browser': {'workflow': 'quests'}}
    setup_owner(monkeypatch, record)
    evidence = quests.instance_status_evidence(response(run), quests.REWARD_RESOURCE_SPECS[1], 42)
    assert evidence['active_run_observed'] and not evidence['verified']
    monkeypatch.setattr(quests, 'inspect_reward_statuses', AsyncMock(side_effect=quests.RewardStatusNeedsReview(evidence)))
    ok, _ = asyncio.run(quests.run_once(42, status_only=True, reward_batch=True, daily_id='bloodland'))
    assert not ok and record['phase'] == 'held' and record['gameplay_uncertain'] is True
    assert record['pending_instance'] == evidence
    assert not quests.same_instance_read_review(record, 42, 'bloodland')


@pytest.mark.parametrize('obstruction', [
    {'phase': 'running'}, {'phase': 'loading'}, {'pending': {'operationId': 'old'}},
    {'gather': {'gatherId': 'old'}},
])
def test_instance_read_cannot_bypass_a_live_worker_or_pending_operation(obstruction):
    pending = quests.instance_status_evidence(response({'id': 'fixture-run'}), quests.REWARD_RESOURCE_SPECS[1], 42)
    record = {'phase': 'held', 'pending_instance': pending, **obstruction}
    assert not quests.same_instance_read_review(record, 42, 'bloodland')


@pytest.mark.parametrize('run', [None, {'id': 'fixture-run'}, {'id': 'another-run'}])
def test_same_instance_review_is_read_only_and_cannot_release_even_an_empty_slot(monkeypatch, run):
    pending = quests.instance_status_evidence(response({'id': 'fixture-run'}), quests.REWARD_RESOURCE_SPECS[1], 42)
    record = {'phase': 'held', 'gameplay_uncertain': True, 'pending_instance': pending,
              'browser': {'workflow': 'quests'}}
    setup_owner(monkeypatch, record)
    monkeypatch.setattr(quests, 'build_miniapp_http_request', Mock())
    flow = AsyncMock(return_value=SimpleNamespace(ok=True, data=response(run)))
    monkeypatch.setattr(quests, 'run_miniapp_blocking_flow', flow)
    old = copy.deepcopy(pending)
    ok, _ = asyncio.run(quests.run_once(42, status_only=True, reward_batch=True, daily_id='bloodland'))
    assert not ok and record['gameplay_uncertain'] is True and record['phase'] == 'held'
    assert record['pending_instance'] == old
    assert record['instance_review']['run_id'] == (run or {}).get('id')
    assert flow.await_count == 1
    request = quests.build_miniapp_http_request.call_args.args
    assert request[1].endswith('/blood-land') and request[2]['action'] == 'status'
