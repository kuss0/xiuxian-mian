"""Exercise the audit CLI only against temporary SQLite and capture fixtures."""
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'tools/audit_channel_retreat_cycle.py'


@pytest.mark.parametrize('fault', [
    None, 'wrong_player', 'no_settle', 'start_before_settle', 'failed_start',
    'unknown', 'unverified', 'unhandled', 'expired', 'stale', 'future_state',
    'disabled', 'group_enabled', 'force_exit', 'missing_role',
    'future_capture', 'unknown_panel', 'conflicting_panel', 'failed_panel',
    'failed_state', 'wrong_parser',
])
def test_cycle_acceptance(tmp_path, fault):
    now = time.time()
    state_dir = tmp_path / 'data/state'
    captures = state_dir / 'miniapp_capture'
    captures.mkdir(parents=True)
    db = state_dir / 'chaogu_state.db'
    date = dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(hours=8))).date()
    identities = list(range(100, 119))
    records = {}
    rows = []
    with sqlite3.connect(db) as conn:
        conn.executescript('''
            CREATE TABLE meta(key TEXT, value TEXT);
            CREATE TABLE identities(send_as_id INTEGER, username TEXT, enabled INTEGER);
            CREATE TABLE identity_module_state(send_as_id INTEGER, deep_retreat_enabled INTEGER);
            CREATE TABLE identity_runtime_state(send_as_id INTEGER, deep_retreat_phase TEXT);
        ''')
        for ident in identities:
            active_fault = fault if ident == identities[0] else None
            state = dict(action='start', identity_verified=True, outcome_unknown=False,
                         ok=True, parser_version=2, sync=dict(handled=True, phase='running'),
                         snapshot=dict(active=True, known=True, ok=True, conflicting=False,
                                       end_ms=(now + 3600) * 1000))
            record = dict(updated_at=now - 30, state=state)
            if active_fault == 'unknown':
                state['outcome_unknown'] = True
            if active_fault == 'unverified':
                state['identity_verified'] = False
            if active_fault == 'unhandled':
                state['sync']['handled'] = False
            if active_fault == 'expired':
                state['snapshot']['end_ms'] = (now - 1) * 1000
            if active_fault == 'stale':
                record['updated_at'] = now - 8 * 3600
            if active_fault == 'future_state':
                record['updated_at'] = now + 3600
            if active_fault == 'unknown_panel':
                state['snapshot']['known'] = False
            if active_fault == 'conflicting_panel':
                state['snapshot']['conflicting'] = True
            if active_fault == 'failed_panel':
                state['snapshot']['ok'] = False
            if active_fault == 'failed_state':
                state['ok'] = False
            if active_fault == 'wrong_parser':
                state['parser_version'] = 1
            records[f'{ident}:cave_deep_retreat'] = record
            if active_fault != 'missing_role':
                conn.execute('INSERT INTO identities VALUES (?,?,?)',
                             (ident, f'role{ident}', int(active_fault == 'group_enabled')))
                conn.execute('INSERT INTO identity_module_state VALUES (?,?)',
                             (ident, int(active_fault != 'disabled')))
                conn.execute('INSERT INTO identity_runtime_state VALUES (?,?)', (ident, 'running'))
            for action, timestamp in [('settle', now - 20), ('start', now - 10)]:
                if action == 'settle' and active_fault == 'no_settle':
                    continue
                if action == 'start' and active_fault == 'start_before_settle':
                    timestamp = now - 40
                if active_fault == 'future_capture':
                    timestamp += 600
                rows.append(dict(source=f'cave_public_deep_retreat:{ident}:deep_{action}',
                                 step_key=f'deep_seclusion:{action}', created_at=timestamp,
                                 ok=not (action == 'start' and active_fault == 'failed_start'),
                                 request=dict(payload=dict(playerId=-1000000000000 - ident
                                                           + int(active_fault == 'wrong_player')))))
            if active_fault == 'force_exit':
                rows.append(dict(rows[-1], step_key='deep_seclusion:force'))
        conn.executemany('INSERT INTO meta VALUES (?,?)', [
            ('channel_send_as_health', json.dumps(dict(restore_identity_ids=identities))),
            ('miniapp_state_records', json.dumps(records)),
        ])
    capture = captures / f'cave_treasure-{date}.jsonl'
    capture.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    original_db = db.read_bytes()
    result = subprocess.run([sys.executable, str(SCRIPT), '--root', str(tmp_path),
                             '--date', str(date)], capture_output=True, text=True)
    assert result.returncode == (0 if fault is None else 1), result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report['accepted'] == (19 if fault is None else 18)
    assert db.read_bytes() == original_db
