import asyncio
from copy import deepcopy
import json
import sqlite3

import pytest

from model import persistence, state
from model.features import fishing_dwelling_runtime as runtime, fishing_dwelling_store as store_module
from model.features import cave_treasure_runtime as cave
from model import ui
from tools import health_observer as health, reconcile_fishing_inventory as maintenance
import test_fishing_caller_lifecycle as lifecycle
from test_fishing_dwelling_journal import context, response

fishing_env = lifecycle.fishing_env
fishing_db = lifecycle.fishing_db


def settle(h, *, legacy=False):
    async def run():
        state.set_storage_bag_records({str(h.identity_id): {'items': {'bait': 5, 'stone': 1}}})
        store = store_module.NativeFishingStore(h.identity_id, h.session['player_id'])
        ledger = store.journal()
        basis = store.basis()
        if legacy:
            basis.pop('items')
        query, _ = ledger.start(context=context(), site_id='west-shore', model_id='ngw', bait_id='bait',
                                now=h.now - 2 * 86400, projection_basis=basis)
        payload = response(settled=True)
        payload['context']['serverNow'] = (h.now - 2 * 86400) * 1000
        ledger.accept(query, payload)
        return store
    return asyncio.run(run())


@pytest.mark.parametrize('change', ['unrelated', 'fish', 'bait', 'facts', 'legacy'])
def test_new_receipt_accepts_only_unrelated_inventory_changes(fishing_db, change):
    h = fishing_db
    store = settle(h, legacy=change == 'legacy')
    bag = deepcopy(state.get_storage_bag_records())
    items = bag[str(h.identity_id)]['items']
    if change in {'unrelated', 'legacy'}:
        items['stone'] = 37
    elif change == 'facts':
        h.identity['fishing_daily_count'] += 1
    else:
        items['fish' if change == 'fish' else 'bait'] = 2
    state.set_storage_bag_records(bag)

    async def run():
        current = store_module.NativeFishingStore(h.identity_id, h.session['player_id'])
        if change == 'unrelated':
            assert current.project(h.now, update_schedule=False)
            assert not current.project(h.now, update_schedule=False)
            assert state.get_storage_bag_records()[str(h.identity_id)]['items'] == {'bait': 5, 'stone': 37, 'fish': 2}
            assert h.identity[store_module.STATE_KEY]['phase'] == 'accounted'
        else:
            before = deepcopy(state.get_storage_bag_records())
            with pytest.raises(ValueError, match='projection_basis_changed'):
                current.project(h.now)
            assert state.get_storage_bag_records() == before
            assert h.identity[store_module.STATE_KEY]['phase'] == 'settled'
    asyncio.run(run())


def test_held_receipt_stops_background_and_entry_reads_but_not_explicit_recovery(fishing_env, monkeypatch):
    h = fishing_env
    settle(h, legacy=True)
    bag = deepcopy(state.get_storage_bag_records())
    bag[str(h.identity_id)]['items']['stone'] = 37
    state.set_storage_bag_records(bag)
    state.set_miniapp_auto_config({'cave_public_entry_url': h.url, 'cave_public_fishing_enabled': True,
                                  'cave_public_fishing_identity_ids': [h.identity_id]})
    h.identity['next_fishing_time'] = h.now - 1
    assert runtime.projection_held(h.identity)
    assert not ui._cave_public_background_action_due('fishing', h.identity_id, h.now)
    calls = []

    async def load(*args, **kwargs):
        calls.append(True)
        return {'ok': False, 'error': 'fixture_read_stopped'}
    monkeypatch.setattr(cave, '_load_cave_public_identity_session', load)
    result = asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url))
    assert result['extra']['status'] == 'native_projection_conflict'
    assert not calls
    asyncio.run(cave.run_cave_public_fishing(h.identity_id, h.url, native_recovery_only=True))
    assert calls == [True]


def test_native_receipt_and_projection_error_are_visible_to_health():
    for error in ('native_projection_basis_changed', 'native_store_unavailable'):
        assert health.module_error_needs_attention(error)
    details, evidence = [], {}
    assert health.summarize_json_state('fishing_native_operation', {'phase': 'settled'}, 1000, details, evidence) == (True, True)
    assert health.summarize_json_state('fishing_native_operation', {'phase': 'accounted'}, 1000, [], {}) == (False, False)


@pytest.mark.parametrize('level', ['core', 'overview', 'deferred'])
def test_upstream_deferred_inventory_cannot_clear_local_bag(level):
    from model.features.cave_treasure_miniapp import parse_cave_inventory_snapshot
    payload = {'snapshot': {'level': level}, 'account': {'playerId': 7,
               'bagTreasure': {'items': [], 'materials': [], 'treasures': [], 'active': []}}}
    with pytest.raises(ValueError, match='占位'):
        parse_cave_inventory_snapshot(payload, expected_player_id=7)


def proof_for(h):
    record = h.identity[store_module.STATE_KEY]
    account = record['account_id']
    return {'status': 'read_returned', 'read': 'inventory', 'identity_id': h.identity_id,
            'account_id': account, 'player_id': h.identity_id, 'started_at': h.now - 10,
            'steps': [{'endpoint': endpoint, 'status': 200, 'ok': True, 'at': h.now - 9 + index,
                       **({'player_id': h.identity_id} if index else {})}
                      for index, endpoint in enumerate(('start', 'start', 'details', 'section'))],
            'inventory_snapshot': {'complete': True, 'player_id': h.identity_id,
                                   'items': {'fish': 73, 'bait': 8, 'stone': 200},
                                   'sections': {'法宝/丹药/杂物': {'fish': 73, 'bait': 8}, '材料': {'stone': 200}}}}


def maintenance_fixture(h):
    settle(h, legacy=True)
    record = h.identity[store_module.STATE_KEY]
    record['player_id'] = h.identity_id
    assert persistence.save_state()
    runtime_row = dict(h.identity, send_as_id=h.identity_id)
    runtime_row['fishing_native_operation'] = json.dumps(record)
    return runtime_row, record, proof_for(h)


@pytest.mark.parametrize('bad', ['wrong_owner', 'stale', 'incomplete', 'fraction', 'totals', 'facts', 'cast', 'supply'])
def test_maintenance_rejects_ambiguous_evidence(fishing_db, bad):
    h = fishing_db
    runtime_row, record, proof = maintenance_fixture(h)
    if bad == 'wrong_owner':
        proof['player_id'] += 1
    elif bad == 'stale':
        proof['started_at'] -= 300
    elif bad == 'incomplete':
        proof['inventory_snapshot']['complete'] = False
    elif bad == 'fraction':
        proof['inventory_snapshot']['sections']['材料']['stone'] = 200.0
    elif bad == 'totals':
        proof['inventory_snapshot']['items']['fish'] = 74
    elif bad == 'facts':
        runtime_row['fishing_daily_count'] += 1
    elif bad == 'supply':
        runtime_row['fishing_native_supply'] = json.dumps({'phase': 'accounted'})
    else:
        record['cast_id'] = '0' * 32
    with pytest.raises(ValueError):
        maintenance.build_plan(runtime_row, proof, account_id=record['account_id'],
                               expected_cast=record['cast_id'], now=h.now)


@pytest.mark.parametrize('worker_restart', [False, True])
def test_maintenance_archives_and_applies_absolute_balances_atomically(fishing_db, monkeypatch, tmp_path, worker_restart):
    h = fishing_db
    runtime_row, record, proof = maintenance_fixture(h)
    states = iter([True, not worker_restart])
    monkeypatch.setattr(maintenance, 'worker_stopped', lambda: next(states))
    with sqlite3.connect(persistence.DB_FILE) as db:
        before = db.execute("SELECT value FROM meta WHERE key='storage_bag_records'").fetchone()[0]
    kwargs = dict(expected_cast=record['cast_id'], apply=True, backup=tmp_path / 'backup.db', now=h.now)
    if worker_restart:
        with pytest.raises(ValueError, match='worker_restarted'):
            maintenance.reconcile(persistence.DB_FILE, proof, **kwargs)
    else:
        result = maintenance.reconcile(persistence.DB_FILE, proof, **kwargs)
        assert result['applied'] and result['today_rods_added'] == 0
    with sqlite3.connect(persistence.DB_FILE) as db:
        saved = json.loads(db.execute('SELECT fishing_native_operation FROM identity_runtime_state WHERE send_as_id=?',
                                      (h.identity_id,)).fetchone()[0])
        bags = db.execute("SELECT value FROM meta WHERE key='storage_bag_records'").fetchone()[0]
        if worker_restart:
            assert saved['phase'] == 'settled' and bags == before
        else:
            assert saved['phase'] == 'accounted'
            assert json.loads(bags)[str(h.identity_id)]['items']['fish'] == 73
            archive = json.loads(db.execute('SELECT value FROM meta WHERE key=?', (result['archive_key'],)).fetchone()[0])
            assert archive['original_operation'] == record and not archive['estimated_gains_applied']
            with pytest.raises(ValueError):
                maintenance.reconcile(persistence.DB_FILE, proof, expected_cast=record['cast_id'], now=h.now)
