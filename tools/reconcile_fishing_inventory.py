"""Explicit old-settlement reconciliation from a fresh complete server inventory.

No casts, hooks, game mutations or estimated gains. The worker must be stopped
for apply; archive, absolute inventory and accounted marker commit together.
"""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.features.fishing_dwelling_journal import settlement_complete, validate
from model.features.fishing_dwelling_supply import validate as validate_supply

TZ = ZoneInfo('Asia/Shanghai')
FACTS = ('fishing_daily_day', 'fishing_daily_count', 'fishing_daily_limit',
         'fishing_daily_catch_summary_json', 'fishing_basket_calibrated_day')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def clock(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def day(value):
    return datetime.fromtimestamp(value, TZ).strftime('%Y-%m-%d')


def build_plan(runtime, proof, *, account_id, expected_cast, now):
    require(clock(now), 'invalid_clock')
    identity = runtime['send_as_id']
    require(type(identity) is int and identity > 0, 'regular_identity_required')
    record = validate(json.loads(runtime['fishing_native_operation']))
    require(record['cast_id'] == expected_cast, 'cast_changed')
    require(record['identity_id'] == identity and record['account_id'] == account_id
            and record['player_id'] == identity, 'receipt_owner_mismatch')
    require(record['phase'] == 'settled' and settlement_complete(record), 'confirmed_settlement_required')
    require(day(record['created_at']) < day(now) and record['settlement_quota']['day'] < day(now), 'prior_day_only')
    require(json.loads(runtime.get('fishing_operation') or '{}') == {}
            and json.loads(runtime.get('fishing_result_pending') or '{}') == {}, 'legacy_fishing_unresolved')
    supply = json.loads(runtime.get('fishing_native_supply') or '{}')
    if supply:
        validate_supply(supply)
        require(supply['phase'] == 'accounted' and
                tuple(supply[key] for key in ('identity_id', 'account_id', 'player_id')) == (identity, account_id, identity),
                'supply_unresolved_or_foreign')
    facts = {key: runtime.get(key) for key in FACTS}
    digest = hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    require(digest == record['projection_basis']['facts'], 'fishing_facts_changed')
    require(proof.get('status') == 'read_returned' and proof.get('read') == 'inventory', 'inventory_read_required')
    require(all(type(proof.get(key)) is int for key in ('identity_id', 'account_id', 'player_id'))
            and proof['player_id'] == identity
            and proof.get('identity_id') == identity and proof.get('account_id') == account_id, 'read_owner_mismatch')
    steps = proof.get('steps')
    require(isinstance(steps, list) and [row.get('endpoint') for row in steps]
            == ['start', 'start', 'details', 'section'], 'read_sequence_invalid')
    require(all(row.get('status') == 200 and row.get('ok') is True and clock(row.get('at')) for row in steps),
            'read_not_confirmed')
    require(all(type(row.get('player_id')) is int and row['player_id'] == identity for row in steps[1:]),
            'selected_player_mismatch')
    started, observed = proof.get('started_at'), steps[-1]['at']
    require(clock(started) and record['created_at'] < started <= steps[0]['at']
            and all(a['at'] <= b['at'] for a, b in zip(steps, steps[1:]))
            and observed <= now <= started + 120, 'read_not_fresh')
    snapshot = proof.get('inventory_snapshot')
    require(isinstance(snapshot, dict) and snapshot.get('complete') is True
            and type(snapshot.get('player_id')) is int and snapshot['player_id'] == identity, 'snapshot_incomplete')
    items, sections = snapshot.get('items'), snapshot.get('sections')
    require(isinstance(items, dict) and len(items) <= 2000 and isinstance(sections, dict)
            and set(sections) == {'法宝/丹药/杂物', '材料'}, 'snapshot_fields_invalid')
    combined = {}
    for section in sections.values():
        require(isinstance(section, dict) and len(section) <= 2000, 'snapshot_section_invalid')
        for name, count in section.items():
            require(isinstance(name, str) and name.strip() == name and 0 < len(name) <= 200
                    and type(count) is int and 0 < count <= 2**53 - 1, 'snapshot_count_invalid')
            combined[name] = combined.get(name, 0) + count
    require(items == combined and all(type(v) is int for v in items.values()), 'snapshot_totals_invalid')
    updated = {**record, 'phase': 'accounted', 'revision': record['revision'] + 1}
    validate(updated)
    archive = {'version': 1, 'reason': 'old_confirmed_settlement_authoritative_inventory',
               'original_operation': deepcopy(record), 'observed_at': observed, 'reconciled_at': now,
               'proof_sha256': hashlib.sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
               'estimated_gains_applied': False, 'today_rods_added': 0}
    return {'identity_id': identity, 'record': updated, 'archive': archive,
            'archive_key': f'fishing_reconciled_inventory:{identity}:{expected_cast}',
            'snapshot': snapshot, 'next_at': now + 10}


def worker_stopped():
    result = subprocess.run(['systemctl', 'show', 'xiuxian.service', '-p', 'ActiveState', '-p', 'MainPID', '--value'],
                            text=True, capture_output=True, check=True)
    return set(result.stdout.splitlines()) == {'0', 'inactive'}


def reconcile(db_path, proof, *, expected_cast, apply=False, backup=None, now=None):
    now = time.time() if now is None else now
    if apply:
        require(worker_stopped(), 'worker_must_be_stopped')
        require(backup is not None and not Path(backup).exists(), 'new_backup_path_required')
    with sqlite3.connect(Path(db_path).resolve().as_uri() + ('?mode=rw' if apply else '?mode=ro'), uri=True) as db:
        db.row_factory = sqlite3.Row
        if apply:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with sqlite3.connect(backup) as dest:
                db.backup(dest)
                require(dest.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'backup_invalid')
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        identity = proof.get('identity_id')
        runtime = db.execute('SELECT * FROM identity_runtime_state WHERE send_as_id=?', (identity,)).fetchone()
        require(runtime is not None, 'identity_missing')
        accounts = json.loads(db.execute("SELECT value FROM meta WHERE key='identity_account_map'").fetchone()[0])
        require(db.execute('SELECT count(*) FROM pending_tasks WHERE send_as_id=?', (identity,)).fetchone()[0] == 0,
                'active_transport_pending')
        plan = build_plan(dict(runtime), proof, account_id=accounts.get(str(identity)), expected_cast=expected_cast, now=now)
        require(db.execute('SELECT 1 FROM meta WHERE key=?', (plan['archive_key'],)).fetchone() is None, 'already_reconciled')
        if apply:
            bags = json.loads(db.execute("SELECT value FROM meta WHERE key='storage_bag_records'").fetchone()[0])
            bag = deepcopy(bags.get(str(identity)) or {})
            bag.update(identity_id=identity, items=plan['snapshot']['items'], sections=plan['snapshot']['sections'],
                       empty=not bool(plan['snapshot']['items']), updated_at=plan['archive']['observed_at'],
                       updated_at_text=datetime.fromtimestamp(plan['archive']['observed_at'], TZ).isoformat(),
                       source='cave_inventory_miniapp')
            bags[str(identity)] = bag
            db.execute('UPDATE meta SET value=? WHERE key=?', (json.dumps(bags, ensure_ascii=False), 'storage_bag_records'))
            db.execute('INSERT INTO meta(key,value) VALUES(?,?)', (plan['archive_key'], json.dumps(plan['archive'], ensure_ascii=False)))
            db.execute('UPDATE identity_runtime_state SET fishing_native_operation=?, fishing_last_error=?, fishing_last_result=? WHERE send_as_id=?',
                       (json.dumps(plan['record'], ensure_ascii=False), '', '旧钓鱼回执已按服务器库存核对，未重复计奖', identity))
            db.execute('UPDATE identity_timers SET next_fishing_time=? WHERE send_as_id=?', (plan['next_at'], identity))
            require(worker_stopped(), 'worker_restarted_during_reconciliation')
            db.commit()
    return {'applied': apply, 'identity_id': identity, 'archive_key': plan['archive_key'],
            'reason': plan['archive']['reason'], 'item_count': len(plan['snapshot']['items']), 'today_rods_added': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--expected-cast', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    try:
        result = reconcile(args.db, json.loads(args.proof.read_text()), expected_cast=args.expected_cast,
                           apply=args.apply, backup=args.backup)
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'applied': False, 'refused': str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
