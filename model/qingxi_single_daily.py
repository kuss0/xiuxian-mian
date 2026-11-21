"""Pure proof checks for one manually selected native rewarded dungeon."""
import re

SINGLE_DAILY_RUN_SEC = 3600
SINGLE_DAILY_WORKER_SEC = 90 + 650 + SINGLE_DAILY_RUN_SEC + 60 + 240

DAILY = {
    'moonpalace': ('moon-palace', '月殿血誓'),
    'bloodland': ('blood-land', '血色禁地'),
    'southgate': ('south-gate', '南门守城'),
    'escort': ('refugee-escort', '护民出关'),
    'mulanshrine': ('mulan-shrine', '血祭神坛'),
    'bqpuppet': ('baiqiao-puppet', '试傀场'),
    'jianzhong': ('jianzhong-trial', '剑冢'),
}


def valid_daily_id(value):
    return isinstance(value, str) and value in DAILY


def defeated_daily_today(record, day):
    runs = record.get('reward_one_runs') or {}
    if not isinstance(runs, dict):
        return True  # Corrupt attempt history cannot authorize another batch.
    for daily_id, proof in runs.items():
        if not valid_daily_id(daily_id) or not isinstance(proof, dict):
            return True
        if proof.get('outcome') == 'defeat':
            final = proof.get('final')
            if (proof.get('daily_id') != daily_id or proof.get('phase') != 'partial'
                    or not isinstance(final, dict) or not isinstance(final.get('day'), str)):
                return True
            if final['day'] == day:
                return True
    return False


def _snapshot(snapshot, identity_id):
    if (not isinstance(snapshot, dict) or snapshot.get('verified') is not True
            or snapshot.get('player') != identity_id
            or not isinstance(snapshot.get('day'), str)
            or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', snapshot['day'])):
        return None
    rows = snapshot.get('daily')
    if (not isinstance(rows, list) or len(rows) != len(DAILY)
            or any(not isinstance(row, dict) or not valid_daily_id(row.get('id')) for row in rows)
            or {row['id'] for row in rows} != set(DAILY)):
        return None
    if any(row.get('day') != snapshot['day'] or type(row.get('hasKey')) is not bool
           or type(row.get('claimed')) is not bool or 'activeRun' not in row or row['activeRun'] is not None
           or ('exhausted' in row and type(row['exhausted']) is not bool) for row in rows):
        return None
    trials = snapshot.get('trials')
    if not isinstance(trials, dict) or set(trials) != {'longwind', 'bamboo'}:
        return None
    for arena, quota in trials.items():
        if (not isinstance(quota, dict) or quota.get('arena') != arena or quota.get('day') != snapshot['day']
                or type(quota.get('limit')) is not int or quota['limit'] != 3
                or any(type(quota.get(key)) is not int or quota[key] < 0 for key in ('used', 'remaining'))
                or quota['used'] + quota['remaining'] != 3
                or 'activeRun' not in quota or quota['activeRun'] is not None):
            return None
    return {row['id']: row for row in rows}


def verify_single_daily(result, identity_id, daily_id):
    """A safely closed single attempt; claimed, defeated and skipped stay distinct.

    This verifies browser/server facts. The owning host must additionally read
    fresh flight status before releasing its public-entry lock and uncertainty.
    """
    if (type(identity_id) is not int or identity_id <= 0 or not valid_daily_id(daily_id)
            or not isinstance(result, dict) or type(result.get('player')) is not int or result.get('player') != identity_id
            or result.get('server_checked') is not True or type(result.get('pending')) is not int
            or result['pending'] != 0 or result.get('stored_pending') != []
            or any(result.get(key) for key in ('error', 'status_error', 'cast_error', 'cleanup_error',
                                              'snapshot_error', 'browser_shutdown_error', 'browser_close_error'))):
        return False
    proof = result.get('reward_one')
    if (not isinstance(proof, dict) or proof.get('mode') != 'single_daily' or proof.get('daily_id') != daily_id
            or proof.get('phase') not in ('complete', 'partial') or proof.get('running') is not False
            or proof.get('error')):
        return False
    before, final = proof.get('before'), proof.get('final')
    initial_rows, final_rows = _snapshot(before, identity_id), _snapshot(final, identity_id)
    if initial_rows is None or final_rows is None or before['day'] != final['day']:
        return False
    for key in DAILY:
        if key == daily_id:
            continue
        if any(initial_rows[key].get(field, False) != final_rows[key].get(field, False)
               for field in ('claimed', 'hasKey', 'exhausted')):
            return False
    for arena in ('longwind', 'bamboo'):
        if any(before['trials'][arena][key] != final['trials'][arena][key] for key in ('used', 'remaining', 'limit')):
            return False
    initial, after = initial_rows[daily_id], final_rows[daily_id]
    receipts = proof.get('receipts')
    if not isinstance(receipts, list):
        return False
    eligible = initial['hasKey'] and not initial['claimed'] and not initial.get('exhausted', False)
    if proof.get('outcome') == 'skipped':
        reason = ('claimed' if initial['claimed'] else 'locked' if not initial['hasKey']
                  else 'exhausted' if initial.get('exhausted') else None)
        return (not eligible and proof.get('phase') == 'partial' and proof.get('reason') == reason
                and proof.get('run_id') is None and not receipts
                and all(initial.get(field, False) == after.get(field, False)
                        for field in ('claimed', 'hasKey', 'exhausted')))
    run_id = proof.get('run_id')
    if (not eligible or not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,120}', run_id)
            or after['hasKey'] is not True or len(receipts) != 2):
        return False
    start, end = receipts
    if any(not isinstance(row, dict) or row.get('ok') is not True
           or row.get('resource') != DAILY[daily_id][0] or row.get('run_id') != run_id
           or not isinstance(row.get('operation_id'), str) or not row['operation_id']
           or row.get('player_id') != identity_id or row.get('error') for row in receipts):
        return False
    if (start.get('action') != 'start' or start.get('run_rewarded') is not True
            or end.get('action') != 'end' or start['operation_id'] == end['operation_id']):
        return False
    grants, cultivation = end.get('grants'), end.get('cultivation')
    if (not isinstance(grants, list) or any(not isinstance(row, dict) or not isinstance(row.get('name'), str)
                                          or not row['name'].strip() or type(row.get('quantity')) is not int
                                          or row['quantity'] <= 0 for row in grants)
            or type(cultivation) is not int or cultivation < 0):
        return False
    if proof.get('outcome') == 'claimed':
        return (proof['phase'] == 'complete' and after['claimed'] is True
                and end.get('result_kind') == 'clear' and end.get('result_rewarded') is True
                and bool(grants or cultivation))
    return (proof.get('outcome') == 'defeat' and proof['phase'] == 'partial' and after['claimed'] is False
            and end.get('request_result') == 'defeat' and end.get('result_kind') in ('failure', 'defeat'))
