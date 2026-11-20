"""Replay the real decoded takeoff rejection without a game request or live DB."""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model.features import qingxi_quests as quests


FIXTURE = Path(__file__).parent / 'fixtures/qingxi-flight-start-rate-limited.json'
NOW = 1795156477


def held(tmp_path):
    result = json.loads(FIXTURE.read_text())['result']
    (tmp_path / 'result.json').write_text(json.dumps(result))
    record = {'phase': 'held', 'gameplay_uncertain': True, 'observed_at': NOW - 1,
              'browser': {'phase': 'held', 'error': result['error'], 'evidence_dir': str(tmp_path)}}
    return result, record


@pytest.mark.parametrize('version', ['2.0.53', '2.0.78'])
def test_real_rate_limited_start_requires_fresh_explicit_closed_flight(monkeypatch, tmp_path, version):
    result, record = held(tmp_path)
    result['upstream_version'] = version
    path = tmp_path / 'result.json'
    path.write_text(json.dumps(result))
    original = path.read_bytes()
    monkeypatch.setattr(quests.time, 'time', lambda: NOW)
    monkeypatch.setattr(quests, 'save', Mock())
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': False}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
    assert record['gameplay_uncertain'] is False and record['phase'] == 'waiting_gameplay'
    assert record['browser_review']['reason'] == 'rate_limited_flight_start_closed'
    assert record['browser_review']['rejected_flight_pending'] == result['pending_operations']
    assert path.read_bytes() == original and record['browser']['error'] == result['error']


@pytest.mark.parametrize('taint', [
    'identity', 'browser_error', 'unsupported_version', 'transport', 'navigation', 'running',
    'combat', 'casts', 'reward', 'quest_write', 'extra_pending', 'disk_changed', 'pending_count',
    'request_count', 'http_400', 'http_409', 'http_500', 'non_json', 'parse_abort', 'unknown_error',
    'content_type', 'wrong_operation', 'receipt_before_request', 'no_later_status', 'stale_status',
    'future_status', 'entry_error', 'browser_shutdown_error', 'browser_close_error', 'status_error',
    'settlement', 'unknown_cleanup', 'unsafe_position', 'report_missing', 'unknown_pending_field',
])
def test_rate_limited_start_keeps_polluted_evidence_held(monkeypatch, tmp_path, taint):
    result, record = held(tmp_path)
    rejected = result['receipts'][2]
    if taint == 'identity':
        result['player'] = 43
    elif taint == 'browser_error':
        record['browser']['error'] = 'quest_transport_unconfirmed'
    elif taint == 'unsupported_version':
        result['upstream_version'] = '2.0.79'
    elif taint in {'transport', 'navigation'}:
        result[taint + '_policy'] += 1
    elif taint == 'running':
        result['run']['done'] = False
    elif taint == 'combat':
        result['combat_active'] = True
    elif taint == 'casts':
        result['casts'] = 1
    elif taint == 'reward':
        rejected['grants'] = [{'name': '灵石', 'quantity': 1}]
    elif taint == 'quest_write':
        result['receipts'].append({'resource': 'quests', 'action': 'field-defeat', 'ok': True})
    elif taint == 'extra_pending':
        result['pending_operations'].append({'resource': 'quests', 'action': 'turn-in'})
        result['pending'] = 2
    elif taint == 'disk_changed':
        result['stored_pending'][0]['operation_id'] = 'flight:other'
    elif taint in {'pending_count', 'request_count'}:
        result['pending' if taint == 'pending_count' else 'requests'] += 1
    elif taint.startswith('http_'):
        rejected['http_status'] = int(taint[5:])
    elif taint in {'non_json', 'unknown_error'}:
        rejected['error'] = 'non_json' if taint == 'non_json' else 'flight_unconfirmed'
    elif taint == 'parse_abort':
        rejected['parse_error'] = 'AbortError'
    elif taint == 'content_type':
        rejected['content_type'] = 'text/html'
    elif taint == 'wrong_operation':
        rejected['operation_id'] = 'flight:other'
    elif taint == 'receipt_before_request':
        rejected['at'] = result['pending_operations'][0]['at'] - 1
    elif taint == 'no_later_status':
        result['receipts'] = result['receipts'][:3]
        result['requests'] = 2
    elif taint == 'stale_status':
        record['observed_at'] = NOW - 120
    elif taint == 'future_status':
        record['observed_at'] = NOW + 1
    elif taint in {'entry_error', 'browser_shutdown_error', 'browser_close_error', 'status_error'}:
        result[taint] = 'unconfirmed'
    elif taint == 'settlement':
        result['report']['fieldSettlements'] = [{'ok': True}]
    elif taint == 'unknown_cleanup':
        result['cleanup_error'] = 'unknown'
    elif taint == 'unsafe_position':
        result['position'] = [0, 0, 800]
    elif taint == 'report_missing':
        result.pop('report')
    elif taint == 'unknown_pending_field':
        result['pending_operations'][0]['events'] = ['unknown']
        result['stored_pending'] = copy.deepcopy(result['pending_operations'])
    (tmp_path / 'result.json').write_text(json.dumps(result))
    monkeypatch.setattr(quests.time, 'time', lambda: NOW)
    save = Mock()
    monkeypatch.setattr(quests, 'save', save)
    call = AsyncMock(return_value={'ok': True, 'mana': {'flightLocked': False}})
    asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is True and record['phase'] == 'held'
    assert 'browser_review' not in record
    call.assert_not_awaited()
    save.assert_not_called()


@pytest.mark.parametrize('failure', ['flight_locked', 'not_ok', 'missing_lock', 'wrong_boolean', 'flight_error', 'save_error'])
def test_rate_limited_start_review_and_save_failures_keep_hold(monkeypatch, tmp_path, failure):
    _, record = held(tmp_path)
    monkeypatch.setattr(quests.time, 'time', lambda: NOW)
    response = {'ok': True, 'mana': {'flightLocked': False}}
    if failure == 'flight_locked':
        response['mana']['flightLocked'] = True
    elif failure == 'not_ok':
        response['ok'] = False
    elif failure == 'missing_lock':
        response['mana'] = {}
    elif failure == 'wrong_boolean':
        response['mana']['flightLocked'] = 0
    call = AsyncMock(return_value=response)
    save = Mock()
    if failure == 'flight_error':
        call.side_effect = RuntimeError('flight_unavailable')
    elif failure == 'save_error':
        save.side_effect = RuntimeError('qingxi_state_save_failed')
    monkeypatch.setattr(quests, 'save', save)
    if failure in {'flight_error', 'save_error'}:
        with pytest.raises(RuntimeError):
            asyncio.run(quests.review_browser_hold(call, record, 42))
    else:
        asyncio.run(quests.review_browser_hold(call, record, 42))
    assert record['gameplay_uncertain'] is True and record['phase'] == 'held'
    assert 'browser_review' not in record
    call.assert_awaited_once_with('status', _api_path='/api/miniapp/xianxia-dwelling/flight')


@pytest.mark.parametrize('failure', ['refresh_failure', 'response_identity', 'session_identity'])
def test_real_refresh_path_never_reviews_failed_or_mismatched_identity(monkeypatch, tmp_path, failure):
    from model.features import cave_treasure_runtime as cave
    _, record = held(tmp_path)
    monkeypatch.setattr(quests.time, 'time', lambda: NOW)
    owner = SimpleNamespace(identity_id=42, is_current=lambda: True)
    monkeypatch.setattr(quests.MiniAppIdentityOwner, 'capture', Mock(return_value=owner))
    monkeypatch.setattr(quests, 'get_global_enabled', lambda: True)
    monkeypatch.setattr(quests, 'is_cave_public_identity_available', lambda _: True)
    monkeypatch.setattr(quests, 'get_activity_block_reason', lambda *a, **k: '')
    monkeypatch.setattr(quests, '_entry_url', lambda: 'offline')
    monkeypatch.setattr(quests, 'get_expedition_config', lambda: {'qingxi_quests': {}})
    monkeypatch.setattr(quests, 'records', lambda: {'42': record})
    monkeypatch.setattr(quests, 'save', Mock())
    monkeypatch.setattr(quests, 'build_cave_treasure_miniapp_adapter', Mock())
    monkeypatch.setattr(quests, 'build_pooled_miniapp_transport', Mock())
    monkeypatch.setattr(quests, 'build_miniapp_http_request', Mock())
    monkeypatch.setattr(cave, '_public_entry_lock', lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, '_public_entry_allowed', lambda: True)
    monkeypatch.setattr(cave, '_parse_public_cave_entry_url', lambda _: ('offline', 'offline', ''))
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', AsyncMock(return_value={
        'ok': True, 'player_id': 43 if failure == 'session_identity' else 42, 'init_data': 'offline'}))
    response = SimpleNamespace(ok=failure != 'refresh_failure', data={
        'ok': True, 'playerId': 43, 'quests': {'states': {}}})
    flow = AsyncMock(return_value=response)
    monkeypatch.setattr(quests, 'run_miniapp_blocking_flow', flow)
    review = AsyncMock()
    monkeypatch.setattr(quests, 'review_browser_hold', review)
    ok, _ = asyncio.run(quests.run_once(42, status_only=True))
    assert ok is False and record['gameplay_uncertain'] is True and record['phase'] == 'held'
    assert record['last_status_error'] == {
        'refresh_failure': 'qingxi_request_unconfirmed', 'response_identity': 'qingxi_player_mismatch',
        'session_identity': 'qingxi_identity_unverified'}[failure]
    review.assert_not_awaited()
    assert flow.await_count == (0 if failure == 'session_identity' else 1)
