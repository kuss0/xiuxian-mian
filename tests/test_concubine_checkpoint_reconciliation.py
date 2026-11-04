import copy
import json
import sqlite3

import pytest

from tools import reconcile_concubine_checkpoint as repair


NOW = 1793765000.0
IDENTITY = 8659059191
OP = 'a' * 32


def fixture(kind='tianji'):
    name = '墨彩环' if kind == 'tianji' else '南宫婉·月影'
    command = '.天机代卜' if kind == 'tianji' else '.远航归来'
    old = NOW - 90000
    operation = {'op_id': OP, 'kind': kind, 'identity_id': IDENTITY, 'account_id': IDENTITY,
                 'chat_id': -1002083016447, 'command': command, 'partner': name,
                 'status': 'sent', 'started_at': old, 'dispatch_at': old + 1,
                 'sent_at': old + 2, 'msg_id': 1001, 'snapshot_at': old - 10,
                 'route': '月殿寻痕', 'voyage': {'status': 'sailing'}}
    panel = (f'**1. 你的红尘道侣: 【{name}】** (状态: 随行中)\n'
             '- 天机代卜链: 无\n- 入梦寻图冷却: 可施展\n'
             '- 共历心劫冷却: 可施展\n- 天机代卜冷却: 可施展\n')
    if kind == 'voyage_return':
        panel += '**远航状态**: 月殿寻痕航线已归航，待结算（`.远航归来`）。\n'
    runtime = {'send_as_id': IDENTITY, 'concubine_phase': kind + '_pending',
               'concubine_availability': 'available', 'concubine_name': name,
               'concubine_kind': '红尘道侣', 'concubine_tianji_due_at': old,
               'concubine_last_snapshot_at': old - 10,
               'concubine_last_panel_msg_id': 990, 'concubine_last_panel_chat_id': -1002083016447,
               'concubine_voyage_route': '月殿寻痕',
               'concubine_tianji_msg_id': 1001 if kind == 'tianji' else 0,
               'concubine_voyage_msg_id': 1001 if kind == 'voyage_return' else 0,
               'concubine_tianji_action': json.dumps(operation if kind == 'tianji' else {}),
               'concubine_voyage_actions': json.dumps({'voyage_return': operation, 'voyage': {'status': 'complete'}}
                                                     if kind == 'voyage_return' else {})}
    proof = {'status': 'read_returned', 'read': '.我的侍妾', 'identity_id': IDENTITY,
             'player_id': IDENTITY, 'account_id': IDENTITY, 'started_at': NOW - 10,
             'steps': [{'endpoint': endpoint, 'status': 200, 'ok': True, 'at': NOW - 9 + index,
                        **({'player_id': IDENTITY} if index else {})}
                       for index, endpoint in enumerate(['start', 'start', 'details', 'command-center'])],
             'action': {'ok': True, 'completed': True, 'command': '.我的侍妾', 'rawMessage': panel}}
    return runtime, proof, operation


@pytest.mark.parametrize('kind', ['tianji', 'voyage_return'])
def test_reconciliation_archives_uncertainty_and_requires_new_read(kind):
    runtime, proof, operation = fixture(kind)
    before = copy.deepcopy((runtime, proof))
    plan = repair.build_plan(runtime, proof, account_id=IDENTITY, expected_op=OP, now=NOW)
    assert (runtime, proof) == before
    assert plan['archive']['original_operation'] == operation
    assert plan['archive']['outcome_unknown'] is True
    assert plan['archive']['rewards_inferred'] is False
    assert plan['changes']['concubine_availability'] == 'unknown'
    assert plan['changes']['concubine_last_snapshot_at'] == 0
    assert plan['changes']['concubine_phase'] == 'idle'
    assert not any('enabled' in key or 'gain' in key or 'affinity' in key for key in plan['changes'])
    if kind == 'voyage_return':
        assert json.loads(plan['changes']['concubine_voyage_actions']) == {'voyage': {'status': 'complete'}}


@pytest.mark.parametrize('change', ['identity', 'account', 'player_bool', 'status', 'command', 'incomplete',
                                    'foreign_partner', 'wrong_kind', 'http', 'http_ok', 'step_player',
                                    'future', 'stale', 'sequence', 'new_operation', 'unsent', 'anchor',
                                    'recent', 'phase', 'cooldown', 'chain', 'local_cooldown'])
def test_invalid_or_insufficient_proof_never_releases_blocker(change):
    runtime, proof, _ = fixture()
    if change == 'identity':
        proof['identity_id'] += 1
    elif change == 'account':
        proof['account_id'] += 1
    elif change == 'player_bool':
        proof['player_id'] = True
    elif change == 'status':
        proof['status'] = 'stopped_no_retry'
    elif change == 'command':
        proof['action']['command'] = '.天机代卜'
    elif change == 'incomplete':
        proof['action']['rawMessage'] = '你的红尘道侣: 【墨彩环】 (状态: 随行中)'
    elif change == 'foreign_partner':
        proof['action']['rawMessage'] = proof['action']['rawMessage'].replace('墨彩环', 'other')
    elif change == 'wrong_kind':
        runtime['concubine_kind'] = '道心侍妾'
    elif change in ('http', 'http_ok', 'step_player', 'future'):
        proof['steps'][-1][{'http': 'status', 'http_ok': 'ok', 'step_player': 'player_id', 'future': 'at'}[change]] = {
            'http': 500, 'http_ok': False, 'step_player': IDENTITY + 1, 'future': NOW + 1}[change]
    elif change == 'stale':
        proof['started_at'] = NOW - 121
    elif change == 'sequence':
        proof['steps'].pop(1)
    elif change in ('new_operation', 'unsent', 'recent'):
        op = json.loads(runtime['concubine_tianji_action'])
        op[{'new_operation': 'op_id', 'unsent': 'status', 'recent': 'sent_at'}[change]] = {
            'new_operation': 'b' * 32, 'unsent': 'unknown', 'recent': NOW - 100}[change]
        runtime['concubine_tianji_action'] = json.dumps(op)
    elif change == 'anchor':
        runtime['concubine_tianji_msg_id'] += 1
    elif change == 'phase':
        runtime['concubine_phase'] = 'heart_pending'
    elif change == 'cooldown':
        proof['action']['rawMessage'] = proof['action']['rawMessage'].replace('天机代卜冷却: 可施展', '天机代卜冷却: 2小时')
    elif change == 'chain':
        proof['action']['rawMessage'] = proof['action']['rawMessage'].replace('天机代卜链: 无', '天机代卜链: 心劫前兆（剩余 2小时）')
    elif change == 'local_cooldown':
        runtime['concubine_tianji_due_at'] = NOW + 100
    before = copy.deepcopy(runtime)
    with pytest.raises(repair.ReconciliationRefused):
        repair.build_plan(runtime, proof, account_id=IDENTITY, expected_op=OP, now=NOW)
    assert runtime == before


@pytest.mark.parametrize('change', ['sailing', 'route', 'missing_settlement'])
def test_voyage_requires_current_explicit_same_route_entitlement(change):
    runtime, proof, _ = fixture('voyage_return')
    if change == 'sailing':
        proof['action']['rawMessage'] = proof['action']['rawMessage'].replace('已归航', '进行中')
    elif change == 'route':
        proof['action']['rawMessage'] = proof['action']['rawMessage'].replace('月殿寻痕', 'other')
    else:
        proof['action']['rawMessage'] = proof['action']['rawMessage'].replace('，待结算（`.远航归来`）', '')
    with pytest.raises(repair.ReconciliationRefused):
        repair.build_plan(runtime, proof, account_id=IDENTITY, expected_op=OP, now=NOW)


def database(path):
    runtime, proof, _ = fixture()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE identity_runtime_state (' + ','.join(f'{k} {"TEXT" if isinstance(v,str) else "REAL" if isinstance(v,float) else "INTEGER"}'
                                                                    for k,v in runtime.items()) + ')')
        db.execute('INSERT INTO identity_runtime_state VALUES(' + ','.join('?' for _ in runtime) + ')', tuple(runtime.values()))
        db.execute('CREATE TABLE identity_timers(send_as_id INTEGER, next_concubine_time REAL)')
        db.execute('INSERT INTO identity_timers VALUES(?,?)', (IDENTITY, NOW - 1))
        db.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
        db.execute('INSERT INTO meta VALUES(?,?)', ('identity_account_map', json.dumps({str(IDENTITY): IDENTITY})))
        db.execute('CREATE TABLE pending_tasks(send_as_id INTEGER)')
    return proof


def test_dry_run_does_not_mutate_database(tmp_path):
    path = tmp_path / 'state.db'
    proof = database(path)
    before = path.read_bytes()
    assert repair.reconcile(path, proof, expected_op=OP, now=NOW)['applied'] is False
    assert path.read_bytes() == before


def test_apply_requires_stopped_worker_and_commits_archive_atomically(tmp_path, monkeypatch):
    path, backup = tmp_path / 'state.db', tmp_path / 'before.db'
    proof = database(path)
    monkeypatch.setattr(repair, 'worker_stopped', lambda: False)
    with pytest.raises(repair.ReconciliationRefused, match='worker_must_be_stopped'):
        repair.reconcile(path, proof, expected_op=OP, apply=True, backup=backup, now=NOW)
    assert not backup.exists()
    monkeypatch.setattr(repair, 'worker_stopped', lambda: True)
    result = repair.reconcile(path, proof, expected_op=OP, apply=True, backup=backup, now=NOW)
    assert result['applied']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT concubine_phase,concubine_availability FROM identity_runtime_state').fetchone() == ('idle', 'unknown')
        assert db.execute('SELECT next_concubine_time FROM identity_timers').fetchone()[0] == NOW + 10
        archive = json.loads(db.execute('SELECT value FROM meta WHERE key=?', (result['archive_key'],)).fetchone()[0])
        assert archive['outcome_unknown'] is True
        assert archive['original_operation']['op_id'] == OP
    with sqlite3.connect(backup) as db:
        assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT concubine_phase FROM identity_runtime_state').fetchone()[0] == 'tianji_pending'


def test_worker_restart_before_commit_rolls_back_all_changes(tmp_path, monkeypatch):
    path = tmp_path / 'state.db'
    proof = database(path)
    checks = iter([True, False])
    monkeypatch.setattr(repair, 'worker_stopped', lambda: next(checks))
    with pytest.raises(repair.ReconciliationRefused, match='worker_restarted'):
        repair.reconcile(path, proof, expected_op=OP, apply=True, backup=tmp_path / 'backup.db', now=NOW)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT concubine_phase FROM identity_runtime_state').fetchone()[0] == 'tianji_pending'
        assert db.execute('SELECT count(*) FROM meta').fetchone()[0] == 1


def test_pending_transport_or_changed_owner_blocks_reconciliation(tmp_path):
    path = tmp_path / 'state.db'
    proof = database(path)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO pending_tasks VALUES(?)', (IDENTITY,))
    with pytest.raises(repair.ReconciliationRefused, match='active_transport_pending'):
        repair.reconcile(path, proof, expected_op=OP, now=NOW)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM pending_tasks')
        db.execute("UPDATE meta SET value=? WHERE key='identity_account_map'", (json.dumps({str(IDENTITY): IDENTITY + 1}),))
    with pytest.raises(repair.ReconciliationRefused, match='read_owner_mismatch'):
        repair.reconcile(path, proof, expected_op=OP, now=NOW)
