"""Server-owned single-daily proof; pure data, no browser or network."""
import copy

import pytest

from model.qingxi_single_daily import verify_single_daily


RESOURCES = {
    'moonpalace': 'moon-palace', 'bloodland': 'blood-land', 'southgate': 'south-gate',
    'escort': 'refugee-escort', 'mulanshrine': 'mulan-shrine', 'bqpuppet': 'baiqiao-puppet',
    'jianzhong': 'jianzhong-trial',
}


def statuses():
    return {
        'verified': True, 'player': 42, 'day': '2026-11-21',
        'daily': [dict(id=daily_id, day='2026-11-21', hasKey=True, claimed=False,
                       activeRun=None, exhausted=False) for daily_id in RESOURCES],
        'trials': {arena: dict(arena=arena, day='2026-11-21', used=1, remaining=2,
                               limit=3, activeRun=None) for arena in ('longwind', 'bamboo')},
    }


def row(proof, field, daily_id):
    return next(item for item in proof[field]['daily'] if item['id'] == daily_id)


def claimed_result(daily_id='moonpalace'):
    before = statuses()
    final = copy.deepcopy(before)
    next(item for item in final['daily'] if item['id'] == daily_id)['claimed'] = True
    return {
        'player': 42, 'server_checked': True, 'pending': 0, 'stored_pending': [],
        'reward_one': {
            'mode': 'single_daily', 'daily_id': daily_id, 'phase': 'complete',
            'running': False,
            'outcome': 'claimed', 'before': before, 'final': final, 'run_id': 'fixture-run',
            'receipts': [
                {'ok': True, 'resource': RESOURCES[daily_id], 'action': 'start',
                 'run_id': 'fixture-run', 'run_rewarded': True, 'grants': [], 'cultivation': 0,
                 'operation_id': 'start-operation', 'player_id': 42},
                {'ok': True, 'resource': RESOURCES[daily_id], 'action': 'end',
                 'run_id': 'fixture-run', 'result_kind': 'clear', 'result_rewarded': True,
                 'grants': [{'name': '灵石', 'quantity': 12}], 'cultivation': 100,
                 'operation_id': 'end-operation', 'player_id': 42},
            ],
        },
    }


@pytest.mark.parametrize('daily_id', list(RESOURCES))
def test_every_whitelisted_daily_accepts_only_matching_claimed_proof(daily_id):
    result = claimed_result(daily_id)
    assert verify_single_daily(result, 42, daily_id) is True


@pytest.mark.parametrize('taint', [
    'identity', 'unchecked', 'pending', 'pending_bool', 'disk_pending', 'error',
    'status_error', 'cleanup_error', 'wrong_mode', 'wrong_daily', 'held', 'running',
    'wrong_outcome', 'missing_run', 'empty_run', 'missing_receipts', 'one_receipt',
    'duplicate_start', 'duplicate_end', 'start_failed', 'end_failed', 'start_other_run',
    'end_other_run', 'wrong_start_resource', 'wrong_end_resource', 'start_unrewarded',
    'start_rewarded_unknown', 'end_unrewarded', 'unclear_result', 'missing_grants',
    'missing_cultivation', 'negative_cultivation', 'boolean_cultivation',
    'malformed_grants', 'boolean_quantity', 'negative_quantity', 'empty_gain',
    'target_locked_before', 'target_claimed_before', 'target_exhausted_before',
    'target_unclaimed_after', 'other_claim_changed', 'other_key_changed', 'other_exhausted_changed',
    'before_unverified', 'final_unverified', 'before_player', 'final_player', 'day_changed',
    'missing_daily', 'duplicate_daily', 'missing_trial', 'active_daily', 'active_trial',
    'missing_daily_close', 'missing_trial_close', 'trial_used_changed', 'trial_remaining_changed',
    'proof_error', 'proof_running', 'start_wrong_player', 'end_wrong_player',
    'same_operation_id', 'missing_operation_id', 'zero_quantity',
])
def test_claim_proof_rejects_incomplete_contradictory_or_unrelated_evidence(taint):
    result = claimed_result()
    proof = result['reward_one']
    start, end = proof['receipts']
    other = 'bloodland'
    if taint in {'identity', 'unchecked', 'pending', 'pending_bool', 'disk_pending',
                 'error', 'status_error', 'cleanup_error'}:
        key, value = {
            'identity': ('player', 99), 'unchecked': ('server_checked', False),
            'pending': ('pending', 1), 'pending_bool': ('pending', False),
            'disk_pending': ('stored_pending', [{'operation_id': 'unresolved'}]),
            'error': ('error', 'unknown'), 'status_error': ('status_error', 'unknown'),
            'cleanup_error': ('cleanup_error', 'unknown'),
        }[taint]
        result[key] = value
    elif taint == 'wrong_mode':
        proof['mode'] = 'batch'
    elif taint == 'wrong_daily':
        proof['daily_id'] = other
    elif taint in {'held', 'running'}:
        proof['phase'] = taint
    elif taint == 'wrong_outcome':
        proof['outcome'] = 'victory'
    elif taint == 'missing_run':
        proof.pop('run_id')
    elif taint == 'empty_run':
        proof['run_id'] = ''
    elif taint == 'missing_receipts':
        proof.pop('receipts')
    elif taint == 'one_receipt':
        proof['receipts'].pop()
    elif taint == 'duplicate_start':
        proof['receipts'].append(copy.deepcopy(start))
    elif taint == 'duplicate_end':
        proof['receipts'].append(copy.deepcopy(end))
    elif taint == 'start_failed':
        start['ok'] = False
    elif taint == 'end_failed':
        end['ok'] = False
    elif taint == 'start_other_run':
        start['run_id'] = 'previous-run'
    elif taint == 'end_other_run':
        end['run_id'] = 'previous-run'
    elif taint == 'wrong_start_resource':
        start['resource'] = RESOURCES[other]
    elif taint == 'wrong_end_resource':
        end['resource'] = RESOURCES[other]
    elif taint == 'start_unrewarded':
        start['run_rewarded'] = False
    elif taint == 'start_rewarded_unknown':
        start.pop('run_rewarded')
    elif taint == 'end_unrewarded':
        end['result_rewarded'] = False
    elif taint == 'unclear_result':
        end['result_kind'] = 'victory'
    elif taint == 'missing_grants':
        end.pop('grants')
    elif taint == 'missing_cultivation':
        end.pop('cultivation')
    elif taint == 'negative_cultivation':
        end['cultivation'] = -1
    elif taint == 'boolean_cultivation':
        end['cultivation'] = True
    elif taint == 'malformed_grants':
        end['grants'] = {'灵石': 12}
    elif taint == 'boolean_quantity':
        end['grants'][0]['quantity'] = True
    elif taint == 'negative_quantity':
        end['grants'][0]['quantity'] = -1
    elif taint == 'empty_gain':
        end.update(grants=[], cultivation=0)
    elif taint == 'target_locked_before':
        row(proof, 'before', 'moonpalace')['hasKey'] = False
    elif taint == 'target_claimed_before':
        row(proof, 'before', 'moonpalace')['claimed'] = True
    elif taint == 'target_exhausted_before':
        row(proof, 'before', 'moonpalace')['exhausted'] = True
    elif taint == 'target_unclaimed_after':
        row(proof, 'final', 'moonpalace')['claimed'] = False
    elif taint in {'other_claim_changed', 'other_key_changed', 'other_exhausted_changed'}:
        key, value = {'other_claim_changed': ('claimed', True), 'other_key_changed': ('hasKey', False),
                      'other_exhausted_changed': ('exhausted', True)}[taint]
        row(proof, 'final', other)[key] = value
    elif taint == 'before_unverified':
        proof['before']['verified'] = False
    elif taint == 'final_unverified':
        proof['final']['verified'] = False
    elif taint == 'before_player':
        proof['before']['player'] = 99
    elif taint == 'final_player':
        proof['final']['player'] = 99
    elif taint == 'day_changed':
        proof['final']['day'] = '2026-11-22'
    elif taint == 'missing_daily':
        proof['final']['daily'].pop()
    elif taint == 'duplicate_daily':
        proof['final']['daily'][1] = copy.deepcopy(proof['final']['daily'][0])
    elif taint == 'missing_trial':
        proof['final']['trials'].pop('bamboo')
    elif taint == 'active_daily':
        row(proof, 'final', other)['activeRun'] = {'id': 'unresolved'}
    elif taint == 'active_trial':
        proof['final']['trials']['bamboo']['activeRun'] = {'id': 'unresolved'}
    elif taint == 'missing_daily_close':
        row(proof, 'final', other).pop('activeRun')
    elif taint == 'missing_trial_close':
        proof['final']['trials']['bamboo'].pop('activeRun')
    elif taint == 'trial_used_changed':
        proof['final']['trials']['bamboo'].update(used=2, remaining=1)
    elif taint == 'trial_remaining_changed':
        proof['final']['trials']['longwind'].update(used=0, remaining=3)
    elif taint == 'proof_error':
        proof['error'] = 'unknown'
    elif taint == 'proof_running':
        proof['running'] = True
    elif taint == 'start_wrong_player':
        start['player_id'] = 99
    elif taint == 'end_wrong_player':
        end['player_id'] = 99
    elif taint == 'same_operation_id':
        end['operation_id'] = start['operation_id']
    elif taint == 'missing_operation_id':
        end.pop('operation_id')
    elif taint == 'zero_quantity':
        end['grants'][0]['quantity'] = 0
    else:
        raise AssertionError(taint)
    assert verify_single_daily(result, 42, 'moonpalace') is False


@pytest.mark.parametrize('result', [None, [], 'claimed', 1, True, {}, {'reward_one': []}])
def test_malformed_result_is_a_failed_proof_not_an_exception(result):
    assert verify_single_daily(result, 42, 'moonpalace') is False


@pytest.mark.parametrize('daily_id', [None, '', 'longwind', 'bamboo', 'all', ['moonpalace'], {'id': 'moonpalace'}])
def test_unknown_or_non_scalar_scope_is_a_failed_proof(daily_id):
    assert verify_single_daily(claimed_result(), 42, daily_id) is False


@pytest.mark.parametrize('result_kind', ['failure', 'defeat'])
def test_confirmed_defeat_is_partial_and_does_not_claim_success(result_kind):
    result = claimed_result()
    proof = result['reward_one']
    proof.update(phase='partial', outcome='defeat')
    row(proof, 'final', 'moonpalace')['claimed'] = False
    proof['receipts'][1].update(request_result='defeat', result_kind=result_kind,
                                result_rewarded=False, grants=[], cultivation=0)
    assert verify_single_daily(result, 42, 'moonpalace') is True


@pytest.mark.parametrize('taint', ['claimed_after', 'missing_defeat_request', 'wrong_end_run', 'second_start'])
def test_defeat_requires_matching_closed_receipt_and_never_a_restart(taint):
    result = claimed_result()
    proof = result['reward_one']
    proof.update(phase='partial', outcome='defeat')
    row(proof, 'final', 'moonpalace')['claimed'] = False
    proof['receipts'][1].update(request_result='defeat', result_kind='failure',
                                result_rewarded=False, grants=[], cultivation=0)
    if taint == 'claimed_after':
        row(proof, 'final', 'moonpalace')['claimed'] = True
    elif taint == 'missing_defeat_request':
        proof['receipts'][1].pop('request_result')
    elif taint == 'wrong_end_run':
        proof['receipts'][1]['run_id'] = 'other'
    else:
        proof['receipts'].append(copy.deepcopy(proof['receipts'][0]))
    assert verify_single_daily(result, 42, 'moonpalace') is False


@pytest.mark.parametrize('reason,unavailable', [
    ('claimed', {'claimed': True}), ('locked', {'hasKey': False}),
    ('exhausted', {'exhausted': True}),
])
def test_unavailable_target_is_skipped_without_any_start_or_reward(reason, unavailable):
    result = claimed_result()
    proof = result['reward_one']
    for field in ('before', 'final'):
        row(proof, field, 'moonpalace')['claimed'] = False
        row(proof, field, 'moonpalace').update(unavailable)
    proof.update(phase='partial', outcome='skipped', run_id=None, receipts=[], reason=reason)
    assert verify_single_daily(result, 42, 'moonpalace') is True


@pytest.mark.parametrize('taint', ['eligible', 'receipt', 'run_id', 'changed_other'])
def test_skipped_result_cannot_conceal_an_attempt_or_unrelated_mutation(taint):
    result = claimed_result()
    proof = result['reward_one']
    for field in ('before', 'final'):
        row(proof, field, 'moonpalace')['claimed'] = True
    old_receipt = proof['receipts'][0]
    proof.update(phase='partial', outcome='skipped', run_id=None, receipts=[], reason='claimed')
    if taint == 'eligible':
        for field in ('before', 'final'):
            row(proof, field, 'moonpalace')['claimed'] = False
    elif taint == 'receipt':
        proof['receipts'] = [old_receipt]
    elif taint == 'run_id':
        proof['run_id'] = 'concealed-run'
    else:
        row(proof, 'final', 'bloodland')['claimed'] = True
    assert verify_single_daily(result, 42, 'moonpalace') is False


@pytest.mark.parametrize('path,value', [
    (('reward_one', 'phase'), []), (('reward_one', 'outcome'), {}),
    (('reward_one', 'receipts'), {}), (('reward_one', 'receipts', 0), None),
    (('reward_one', 'receipts', 1, 'operation_id'), []),
    (('reward_one', 'receipts', 1, 'result_kind'), []),
    (('reward_one', 'receipts', 1, 'cultivation'), float('nan')),
    (('reward_one', 'receipts', 1, 'cultivation'), float('inf')),
    (('reward_one', 'receipts', 1, 'grants'), [None]),
    (('reward_one', 'receipts', 1, 'grants'), [{'name': [], 'quantity': 1}]),
    (('reward_one', 'before'), []), (('reward_one', 'final'), []),
    (('reward_one', 'before', 'day'), []),
    (('reward_one', 'before', 'daily'), {}),
    (('reward_one', 'before', 'daily', 0), None),
    (('reward_one', 'before', 'daily', 0, 'id'), []),
    (('reward_one', 'final', 'trials'), []),
    (('reward_one', 'final', 'trials', 'longwind'), None),
    (('reward_one', 'final', 'trials', 'longwind', 'used'), []),
    (('reward_one', 'final', 'trials', 'bamboo', 'limit'), True),
])
def test_malformed_nested_proof_fields_fail_closed_without_raising(path, value):
    result = claimed_result()
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert verify_single_daily(result, 42, 'moonpalace') is False
