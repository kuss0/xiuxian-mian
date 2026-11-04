"""Explicit maintenance reconciliation from a fresh authoritative status read.

Never infer the outcome of an old action or send a game command. Applying a
plan requires a stopped worker, a backup and an exact expected operation ID.
The old uncertain action is archived in SQLite before its blocking slot is
released. Normal scheduling must obtain another status panel before acting.
"""

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model.features import concubine


class ReconciliationRefused(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ReconciliationRefused(reason)


def timestamp(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def build_plan(runtime, proof, *, account_id, expected_op, now):
    """Pure projection; a readiness read supersedes a blocker, not its outcome."""
    require(timestamp(now), 'invalid_clock')
    identity = runtime['send_as_id']
    require(type(identity) is int and identity > 0, 'regular_identity_required')
    require(proof.get('status') == 'read_returned' and proof.get('read') == '.我的侍妾', 'status_read_required')
    require(proof.get('identity_id') == proof.get('player_id') == identity
            and type(proof.get('player_id')) is int
            and type(account_id) is int and account_id > 0
            and proof.get('account_id') == account_id, 'read_owner_mismatch')
    steps = proof.get('steps')
    require(isinstance(steps, list) and [r.get('endpoint') for r in steps]
            == ['start', 'start', 'details', 'command-center'], 'read_sequence_invalid')
    require(all(type(r.get('status')) is int and r['status'] == 200
                and r.get('ok') is True and timestamp(r.get('at')) for r in steps), 'read_not_confirmed')
    require(all(type(r.get('player_id')) is int and r['player_id'] == identity
                for r in steps[1:]), 'selected_player_mismatch')
    started, observed = proof.get('started_at'), steps[-1]['at']
    require(timestamp(started) and started <= steps[0]['at']
            and all(a['at'] <= b['at'] for a, b in zip(steps, steps[1:]))
            and observed <= now <= started + 120, 'read_not_fresh')
    action = proof.get('action') or {}
    require(action.get('ok') is True and action.get('completed') is True
            and action.get('command') == '.我的侍妾', 'panel_not_confirmed')
    text = action.get('rawMessage') or action.get('message')
    require(isinstance(text, str) and len(text) <= 20000, 'invalid_panel_text')
    parsed = concubine._parse_status_panel(text, observed)
    require(concubine._is_complete_status_panel(parsed) and parsed.get('has_partner') is True, 'incomplete_panel')
    require(parsed['name'] == runtime.get('concubine_name')
            and parsed['kind'] == runtime.get('concubine_kind'), 'partner_changed')
    phase = runtime.get('concubine_phase')
    require(phase in ('tianji_pending', 'voyage_return_pending'), 'unsupported_phase')
    kind = 'tianji' if phase == 'tianji_pending' else 'voyage_return'
    field = 'concubine_tianji_action' if kind == 'tianji' else 'concubine_voyage_actions'
    values = json.loads(runtime[field])
    operation = values if kind == 'tianji' else values.get(kind, {})
    require(isinstance(operation, dict) and operation.get('op_id') == expected_op
            and isinstance(expected_op, str) and re.fullmatch('[0-9a-f]{32}', expected_op), 'operation_changed')
    command = '.天机代卜' if kind == 'tianji' else '.远航归来'
    anchor = 'concubine_tianji_msg_id' if kind == 'tianji' else 'concubine_voyage_msg_id'
    require(operation.get('kind') == kind and operation.get('command') == command
            and type(operation.get('identity_id')) is int and operation['identity_id'] == identity
            and type(operation.get('account_id')) is int and operation['account_id'] == account_id
            and operation.get('partner') == parsed['name'], 'operation_owner_mismatch')
    require(operation.get('status') == 'sent' and type(operation.get('msg_id')) is int
            and operation['msg_id'] > 0 and type(runtime.get(anchor)) is int
            and runtime[anchor] == operation['msg_id'], 'sent_anchor_required')
    require(all(timestamp(operation.get(k)) for k in ('started_at', 'dispatch_at', 'sent_at'))
            and operation['started_at'] - 1 <= operation['dispatch_at'] <= operation['sent_at'] < started
            and started - operation['sent_at'] >= 86400, 'operation_not_old_enough')
    if kind == 'tianji':
        require(parsed.get('tianji_due_at') == 0 and parsed.get('tianji_chain') == ''
                and parsed.get('tianji_chain_due_at') == 0, 'divination_not_ready')
        require(float(runtime.get('concubine_tianji_due_at') or 0) <= started, 'newer_local_cooldown')
        updated = {}
        reason = 'fresh_ready_divination_panel_after_prior_cooldown'
    else:
        voyage = parsed.get('voyage') or {}
        require(voyage.get('status') == 'returned' and '待结算' in text
                and '.远航归来' in text, 'voyage_not_explicitly_awaiting_settlement')
        require(voyage.get('route') == operation.get('route') == runtime.get('concubine_voyage_route'), 'voyage_route_changed')
        require((operation.get('voyage') or {}).get('status') in ('sailing', 'returned'), 'old_voyage_missing')
        updated = {k: v for k, v in values.items() if k != kind}
        reason = 'fresh_server_voyage_awaits_settlement'
    changes = {
        field: json.dumps(updated, ensure_ascii=False, sort_keys=True),
        anchor: 0, 'concubine_phase': 'idle', 'concubine_availability': 'unknown',
        'concubine_last_snapshot_at': 0, 'concubine_last_panel_msg_id': 0,
        'concubine_last_panel_chat_id': 0,
    }
    archive = {'schema': 1, 'identity_id': identity, 'account_id': account_id,
               'outcome_unknown': True, 'original_operation': deepcopy(operation),
               'reason': reason, 'observed_at': observed, 'reconciled_at': now,
               'proof_sha256': hashlib.sha256(json.dumps(proof, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
               'next_action': 'fresh_status_read_required', 'rewards_inferred': False}
    return {'identity_id': identity, 'changes': changes, 'archive': archive,
            'archive_key': f'concubine_reconciled_unknown:{identity}:{expected_op}',
            'next_at': now + 10}


def worker_stopped():
    result = subprocess.run(['systemctl', 'show', 'xiuxian.service', '--property=ActiveState',
                             '--property=MainPID', '--value'], check=True, text=True, capture_output=True)
    return set(result.stdout.splitlines()) == {'0', 'inactive'}


def reconcile(db_path, proof, *, expected_op, apply=False, backup=None, now=None):
    now = time.time() if now is None else now
    if apply:
        require(worker_stopped(), 'worker_must_be_stopped')
        require(backup is not None and not Path(backup).exists(), 'new_backup_path_required')
    with sqlite3.connect(f'file:{Path(db_path).resolve()}?mode={"rw" if apply else "ro"}', uri=True) as db:
        db.row_factory = sqlite3.Row
        if apply:
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            with sqlite3.connect(backup) as destination:
                db.backup(destination)
                require(destination.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'backup_invalid')
            db.execute('BEGIN IMMEDIATE')
        identity = proof.get('identity_id')
        runtime_row = db.execute('SELECT * FROM identity_runtime_state WHERE send_as_id=?', (identity,)).fetchone()
        require(runtime_row is not None, 'identity_missing')
        account_map = json.loads(db.execute("SELECT value FROM meta WHERE key='identity_account_map'").fetchone()[0])
        require(db.execute('SELECT count(*) FROM pending_tasks WHERE send_as_id=?', (identity,)).fetchone()[0] == 0,
                'active_transport_pending')
        plan = build_plan(dict(runtime_row), proof, account_id=account_map.get(str(identity)), expected_op=expected_op, now=now)
        require(db.execute('SELECT 1 FROM meta WHERE key=?', (plan['archive_key'],)).fetchone() is None, 'already_reconciled')
        if apply:
            changes = plan['changes']
            db.execute('INSERT INTO meta(key,value) VALUES(?,?)',
                       (plan['archive_key'], json.dumps(plan['archive'], ensure_ascii=False, sort_keys=True)))
            db.execute('UPDATE identity_runtime_state SET ' + ','.join(f'{k}=?' for k in changes) + ' WHERE send_as_id=?',
                       (*changes.values(), identity))
            db.execute('UPDATE identity_timers SET next_concubine_time=? WHERE send_as_id=?', (plan['next_at'], identity))
            require(worker_stopped(), 'worker_restarted_during_reconciliation')
            db.commit()
    return {**plan, 'applied': apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--proof', type=Path, required=True)
    parser.add_argument('--expected-op', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    try:
        result = reconcile(args.db, json.loads(args.proof.read_text()), expected_op=args.expected_op,
                           apply=args.apply, backup=args.backup)
    except ReconciliationRefused as exc:
        print(json.dumps({'applied': False, 'refused': str(exc)}))
        return 1
    print(json.dumps({'applied': result['applied'], 'identity_id': result['identity_id'],
                      'archive_key': result['archive_key'], 'reason': result['archive']['reason'],
                      'next_action': result['archive']['next_action']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
