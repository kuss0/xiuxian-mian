"""Bounded status diagnostics do not add requests or authorize held gameplay."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import qingxi_quests as quests


TARGET = 'lantern:090'
PENDING = {'resource': 'quests', 'action': 'field-release', 'enemy_id': TARGET,
           'operation_id': 'field-release-mwmea4fz-2-2qahwy', 'at': 1795022894.461}


def record(tmp_path):
    result = {'player': 42, 'pending_operations': [copy.deepcopy(PENDING)]}
    (tmp_path / 'result.json').write_text(json.dumps(result))
    return {'phase': 'held', 'gameplay_uncertain': True,
            'browser': {'error': 'quest_http_409_non_json', 'evidence_dir': str(tmp_path)}}


def summarize(data, held):
    return quests.field_status_evidence(data, held, verified_player_id=42, observed_at=100)


def test_status_evidence_keeps_only_pending_target_and_redacts_engagement_tokens(tmp_path):
    held = record(tmp_path)
    data = {'field': {'active': False, 'token': 'private-field-token',
        'engagement': {'active': True, 'enemyId': TARGET, 'generation': 5, 'expiresAt': 120,
                       'token': 'private-engagement-token', 'sessionId': 'private-session'},
        'enemies': [{'id': TARGET, 'generation': 5, 'hp': 13869, 'respawnAt': 0,
                     'active': False, 'engagement': 'private-enemy-token', 'secret': 'private-secret'},
                    {'id': 'lantern:other', 'generation': 999, 'hp': 1, 'respawnAt': 200}]},
        'corpses': [{'id': 'corpse:1', 'enemyId': TARGET, 'generation': 4, 'expiresAt': 130,
                     'looted': False, 'items': [{'itemId': 'private-loot'}], 'token': 'private-corpse-token'},
                    {'id': 'corpse:other', 'enemyId': 'lantern:other', 'items': ['private-other-loot']}],
        'initData': 'private-init-data', 'session': {'token': 'private-session-token'}}
    evidence = summarize(data, held)
    assert evidence['verified_player_id'] == 42 and evidence['observed_at'] == 100
    assert evidence['pending_source'] == 'browser_result'
    assert evidence['field_active'] is False
    assert evidence['field_engagement'] == {'present': True, 'type': 'object', 'active': True,
        'enemy_id': TARGET, 'generation': 5, 'expires_at': 120}
    assert evidence['targets'] == [{'enemy_id': TARGET, 'roster_state': 'present', 'generation': 5,
        'hp': 13869, 'respawn_at': 0, 'active': False,
        'engagement': {'present': True, 'type': 'string'}, 'corpses_state': 'known',
        'corpses': [{'id': 'corpse:1', 'enemy_id': TARGET, 'generation': 4,
                    'expires_at': 130, 'looted': False}], 'corpses_truncated': False}]
    assert 'private-' not in json.dumps(evidence) and 'lantern:other' not in json.dumps(evidence)
    assert held['phase'] == 'held' and held['gameplay_uncertain'] is True


@pytest.mark.parametrize('data', [{}, {'field': None}, {'field': {'regions': {}}},
                                 {'field': {'enemies': 'unknown'}, 'corpses': None}])
def test_missing_authoritative_fields_are_explicitly_unknown(tmp_path, data):
    evidence = summarize(data, record(tmp_path))
    assert evidence['field_active'] == evidence['field_engagement'] == 'unknown'
    target = evidence['targets'][0]
    assert target['enemy_id'] == TARGET and target['roster_state'] == 'unknown'
    for key in ['generation', 'hp', 'respawn_at', 'active', 'engagement', 'corpses_state']:
        assert target[key] == 'unknown'
    assert target['corpses'] == []


def test_absent_or_duplicate_roster_target_does_not_imply_engagement_closed(tmp_path):
    held = record(tmp_path)
    absent = summarize({'field': {'enemies': []}, 'corpses': []}, held)['targets'][0]
    assert absent['roster_state'] == 'absent' and absent['engagement'] == 'unknown'
    assert absent['corpses_state'] == 'known' and absent['corpses'] == []
    duplicate = summarize({'field': {'enemies': [{'id': TARGET, 'generation': 1},
        {'id': TARGET, 'generation': 2}]}, 'corpses': []}, held)['targets'][0]
    assert duplicate['roster_state'] == 'duplicate'
    assert duplicate['generation'] == duplicate['engagement'] == 'unknown'
    assert held['phase'] == 'held' and held['gameplay_uncertain'] is True


@pytest.mark.parametrize('value', [True, -1, float('nan'), float('inf'), '0', None, 10 ** 400])
def test_invalid_numeric_fields_are_unknown(tmp_path, value):
    target = summarize({'field': {'enemies': [{'id': TARGET, 'generation': value,
        'hp': value, 'respawnAt': value}]}}, record(tmp_path))['targets'][0]
    assert target['generation'] == target['hp'] == target['respawn_at'] == 'unknown'


@pytest.mark.parametrize('value', [None, False, True])
def test_explicit_engagement_values_are_preserved_without_inference(tmp_path, value):
    evidence = summarize({'field': {'active': False, 'engagement': value,
        'enemies': [{'id': TARGET, 'generation': 0, 'hp': 0, 'respawnAt': 0, 'engagement': value}]}}, record(tmp_path))
    target = evidence['targets'][0]
    assert evidence['field_engagement'] is value and target['engagement'] is value
    assert target['generation'] == target['hp'] == target['respawn_at'] == 0


def test_bounded_targets_and_corpse_references_do_not_store_whole_rosters(monkeypatch):
    pending = [{'enemy_id': f'lantern:{i:03d}'} for i in range(12)]
    monkeypatch.setattr(quests, '_browser_evidence', lambda _: {'pending_operations': pending})
    data = {'field': {'enemies': [{'id': f'lantern:{i:03d}', 'hp': 100} for i in range(500)]},
            'corpses': [{'id': f'corpse:{i}', 'enemyId': 'lantern:000'} for i in range(12)]}
    evidence = summarize(data, {})
    assert len(evidence['targets']) == 8 and evidence['targets_truncated'] is True
    assert len(evidence['targets'][0]['corpses']) == 8
    assert evidence['targets'][0]['corpses_truncated'] is True
    assert 'lantern:499' not in json.dumps(evidence)


def test_missing_browser_result_can_label_saved_digest_or_host_pending(tmp_path):
    held = {'browser': {'evidence_dir': str(tmp_path), 'evidence': {'pending_operations': [PENDING]}}}
    evidence = summarize({}, held)
    assert evidence['pending_source'] == 'browser_digest' and evidence['targets'][0]['enemy_id'] == TARGET
    evidence = summarize({}, {'pending': {'enemyId': TARGET}})
    assert evidence['pending_source'] == 'host_pending' and evidence['targets'][0]['enemy_id'] == TARGET


def test_unverified_identity_or_invalid_observation_is_not_invented():
    evidence = quests.field_status_evidence({}, {}, verified_player_id=None, observed_at=float('nan'))
    assert evidence['verified_player_id'] == evidence['observed_at'] == 'unknown'
    assert evidence['pending_source'] == 'unknown' and evidence['targets'] == []


@pytest.mark.parametrize('failure', ['', 'wrong_identity', 'failed_status'])
def test_existing_refresh_persists_bounded_evidence_without_extra_requests_or_release(monkeypatch, tmp_path, failure):
    from model.features import cave_treasure_runtime as cave
    held = record(tmp_path)
    original = (tmp_path / 'result.json').read_bytes()
    owner = SimpleNamespace(identity_id=42, is_current=lambda: True)
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', Mock(return_value=owner))
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *a, **k: '')
    monkeypatch.setattr(quests, '_entry_url', lambda: 'offline')
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {'qingxi_quests': {}})
    monkeypatch.setattr(quests, 'records', lambda: {'42': held})
    durable = []
    monkeypatch.setattr(quests, 'save', lambda: durable.append(copy.deepcopy(held)))
    monkeypatch.setattr(quests, 'build_cave_treasure_miniapp_adapter', Mock())
    monkeypatch.setattr(quests, 'build_pooled_miniapp_transport', Mock())
    monkeypatch.setattr(quests, 'build_miniapp_http_request', Mock())
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, '_public_entry_allowed', lambda: True)
    monkeypatch.setattr(cave, '_parse_public_cave_entry_url', lambda _: ('offline', 'offline', ''))
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', AsyncMock(return_value={
        'ok': True, 'player_id': 42, 'init_data': 'private-init-data'}))
    response = {'ok': True, 'playerId': 43 if failure == 'wrong_identity' else 42,
                'quests': {'states': {}, 'field': {'enemies': [{'id': TARGET, 'generation': 5, 'hp': 13869}]},
                           'corpses': []}, 'token': 'private-token'}
    flow = AsyncMock(return_value=SimpleNamespace(ok=failure != 'failed_status', data=response))
    monkeypatch.setattr(quests, 'run_miniapp_blocking_flow', flow)
    ok, _ = asyncio.run(quests.run_once(42, status_only=True))
    assert ok is (not failure)
    assert flow.await_count == 1  # Only the existing quest status read; no extra flight/game request.
    assert held['phase'] == 'held' and held['gameplay_uncertain'] is True
    assert (tmp_path / 'result.json').read_bytes() == original
    if failure:
        assert 'field_status_evidence' not in held
    else:
        evidence = held['field_status_evidence']
        assert evidence['verified_player_id'] == 42 and evidence['observed_at'] > 0
        assert evidence['targets'][0]['generation'] == 5 and evidence['targets'][0]['hp'] == 13869
        assert evidence['field_active'] == evidence['field_engagement'] == 'unknown'
        assert any(snapshot.get('field_status_evidence') == evidence for snapshot in durable)
        assert 'private-' not in json.dumps(evidence)
