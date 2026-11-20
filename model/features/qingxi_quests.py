"""Source-backed Qingxi NPC errands; server state owns completion and rewards.

Combat, fishing and instances are observed, never simulated by this protocol.
Unknown mutations retain their operation ID and block subsequent mutations.
"""
import asyncio
import copy
import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path
import time
import uuid

from ..activity_occupancy import get_activity_block_reason
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, track_background_task
from ..state import (get_expedition_config, get_expedition_run_state, get_global_enabled,
                     is_cave_public_identity_available)
from ..webapp_core import (MiniAppRequestBudget, MiniAppRequestPolicy, build_miniapp_http_request,
                           execute_miniapp_http_request, require_miniapp_operation)
from .changfeng_trial import _entry_url
from .cave_treasure_miniapp import build_cave_treasure_miniapp_adapter
from .miniapp_common import MiniAppIdentityOwner, build_pooled_miniapp_transport, run_miniapp_blocking_flow

API_PATH = '/api/miniapp/xianxia-dwelling/quests'
SUPPORTED_UPSTREAM_VERSIONS = frozenset({'2.0.53', '2.0.78'})
CATALOG_PATH = Path(__file__).with_name('qingxi_quest_catalog.json')
_TASKS = {}


@lru_cache(maxsize=1)
def catalog():
    return json.loads(CATALOG_PATH.read_text())


def records():
    return get_expedition_run_state().setdefault('qingxi_quests', {})


def save():
    mark_dirty()
    if save_state() is False:
        raise RuntimeError('qingxi_state_save_failed')


def fishing_task_site(identity_id, now=None):
    """Use one already-scheduled fishing slot for the actual town objective."""
    now = time.time() if now is None else now
    config = get_expedition_config().get('qingxi_quests', {})
    record = records().get(str(identity_id), {})
    observed_at = record.get('observed_at')
    if (config.get('enabled') and identity_id in config.get('participant_identity_ids', [])
            and type(observed_at) in (int, float) and 0 <= now - observed_at <= 36 * 3600
            and record.get('observed', {}).get('qx_q06', {}).get('status') == 'active'):
        return 'qingxi-riverside'
    return None


def observe_town_catch(identity_id):
    record = records().get(str(identity_id))
    if record is not None and record.get('phase') in {'waiting_gameplay', 'held'}:
        key = 'review_next_at' if record.get('phase') == 'held' else 'next_at'
        record[key] = min(record.get(key) or float('inf'), time.time() + 60)
        try:
            save()  # A task wake-up must not invalidate a confirmed fish receipt.
        except RuntimeError:
            return False
        return True
    return False


async def notify_rewards(identity_id, record):
    attempted = set(record.get('notice_attempted') or [])
    receipts = [r for r in record.get('receipts', []) if (r.get('action') == 'turn-in' or r.get('grants') or r.get('cultivation'))
                and r['operation_id'] not in attempted and r.get('reward_id', r['operation_id']) not in attempted]
    if not receipts:
        return
    gains = {}
    for receipt in receipts:
        for grant in receipt.get('grants') or []:
            name, quantity = grant.get('name'), grant.get('quantity')
            if isinstance(name, str) and type(quantity) is int and quantity > 0:
                gains[name] = gains.get(name, 0) + quantity
        cultivation = receipt.get('cultivation')
        if type(cultivation) is int and cultivation > 0:
            gains['修为'] = gains.get('修为', 0) + cultivation
    record['notice_attempted'] = [*record.get('notice_attempted', []), *[r.get('reward_id', r['operation_id']) for r in receipts]][-160:]
    record['notice_state'] = 'sending'
    save()
    completed = sum(r.get('action') == 'turn-in' for r in receipts)
    message = f'青溪坊任务｜本轮确认交付 {completed} 项'
    reward_batch = (record.get('browser') or {}).get('workflow') == 'rewards'
    if reward_batch:
        message = '青溪每日副本/试炼｜已确认收获'
    if gains:
        message += '｜' + '、'.join(f'{name}×{count}' for name, count in gains.items())
    try:
        sent = await send_audit_log(message, scope='identity', send_as_id=identity_id, limit=700,
                                    **({'priority': 'normal'} if reward_batch else {}))
    except Exception:
        sent = False
    record['notice_state'] = 'sent' if sent else 'unconfirmed'
    save()


def _position(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
        return None
    return [value[0], 0, value[1]]


def plan_next(data, rules, choices=None):
    """Plan one real objective, using server-required quantities and live nodes."""
    states, choices = data.get('states', {}), choices or {}
    region = (data.get('world') or {}).get('current') or 'yue'
    npcs = {n['id']: n for n in rules['npcs']}
    quests = [q for q in rules['quests'] if q.get('region', 'yue') == region]
    known = {q['id'] for q in rules['quests']}
    gaps = [{'quest_id': key, 'title': key, 'reason': 'catalog_not_supported'}
            for key, slot in states.items() if key not in known and slot.get('status') in {'available', 'active', 'ready'}]
    gaps += [{'quest_id': q['id'], 'title': q['title'], 'reason': 'region_travel_required'}
             for q in rules['quests'] if q.get('region', 'yue') != region
             and states.get(q['id'], {}).get('status') in {'available', 'active', 'ready'}]

    def npc_action(action, quest, npc_id):
        position = _position(npcs.get(npc_id, {}).get('positionXZ'))
        if position is None:
            return None
        return {'action': action, 'questId': quest['id'], 'npcId': npc_id,
                'position': position, 'region': region}

    for q in quests:
        slot = states.get(q['id'], {})
        if slot.get('status') != 'ready':
            continue
        options = (q.get('rewards') or {}).get('choices') or []
        if options and choices.get(q['id']) not in {c['id'] for c in options}:
            gaps.append({'quest_id': q['id'], 'title': q['title'], 'reason': 'reward_choice_required',
                         'choices': [{'id': c['id'], 'label': c['label']} for c in options]})
            continue
        action = npc_action('turn-in', q, q['receiver'])
        if action:
            if options:
                action['choice'] = choices[q['id']]
            return action, gaps
    for q in quests:
        slot = states.get(q['id'], {})
        if slot.get('status') != 'active':
            continue
        for objective in q['objectives']:
            need = (slot.get('required') or {}).get(objective['id'], objective.get('required', 1))
            progress = (slot.get('progress') or {}).get(objective['id'], 0)
            if type(need) is not int or type(progress) is not int or need <= 0:
                gaps.append({'quest_id': q['id'], 'reason': 'invalid_objective_quantity'})
                continue
            if progress >= need:
                continue
            if objective['type'] == 'talk':
                action = npc_action('talk', q, objective.get('npc'))
                if action:
                    return action, gaps
            if objective['type'] in {'collect', 'gather', 'interact'} and not objective.get('arena'):
                item = objective.get('itemId') or objective.get('worldObject')
                node = next((n for n in data.get('nodes', []) if n.get('itemId') == item
                             and not n.get('puzzle') and not n.get('aerial')
                             and n.get('region', 'yue') == region and _position(n.get('position'))), None)
                if node:
                    return {'action': 'pickup' if node['id'].startswith('loot:') else 'gather-start',
                            'nodeId': node['id'], 'position': _position(node['position']),
                            'region': region}, gaps
            gaps.append({'quest_id': q['id'], 'title': q['title'], 'objective': objective.get('text'),
                         'objective_type': objective['type'],
                         'progress': progress, 'required': need,
                         'reason': 'gameplay_required' if objective.get('arena') or objective['type'] in {'kill', 'catch', 'instance'}
                         else 'live_node_unavailable', 'arena': objective.get('arena')})
    for q in quests:
        if states.get(q['id'], {}).get('status') == 'available':
            action = npc_action('accept', q, q['giver'])
            if action:
                return action, gaps
    return None, gaps


def browser_required(gaps):
    return any(g.get('reason') not in {'reward_choice_required', 'invalid_objective_quantity'}
               and g.get('objective_type') != 'catch' for g in gaps)


def _browser_evidence(record):
    directory = (record.get('browser') or {}).get('evidence_dir')
    if not directory:
        return {}
    try:
        result = json.loads((Path(directory) / 'result.json').read_text())
    except (OSError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def evidence_digest(result):
    """Unresolved writes and every non-successful receipt, for review without file access."""
    def rows(value, limit):
        return [copy.deepcopy(row) for row in value if isinstance(row, dict)][-limit:] if isinstance(value, list) else None
    receipts = result.get('receipts')
    return {
        'pending_operations': rows(result.get('pending_operations'), 8),
        'stored_pending': rows(result.get('stored_pending'), 8),
        'failed_receipts': rows([row for row in receipts if isinstance(row, dict) and row.get('ok') is not True]
                                if isinstance(receipts, list) else None, 12),
        'last_receipts': [{key: row.get(key) for key in ('resource', 'action', 'ok', 'http_status', 'error',
                                                         'operation_id', 'enemy_id', 'at')}
                          for row in (rows(receipts, 12) or [])],
        **{key: result.get(key) for key in ('state', 'combat_active', 'hp', 'position', 'cast_error',
                                            'transport_policy', 'navigation_policy')},
    }


def legacy_cast_only(record, identity_id):
    """Recognize the retired worker's single ambiguous cast, never quest writes.

    Its control counter includes every quest request and cast. All quest replies
    were retained, including negative replies. Exactly one unmatched request,
    with only successful status receipts, can only be a cast-proof upload.
    A fresh status must still be read before this non-rewarding session is closed.
    """
    browser = record.get('browser') or {}
    if browser.get('error') != 'quest_http_200_unconfirmed' or not browser.get('evidence_dir'):
        return False
    result = _browser_evidence(record)
    receipts = result.get('receipts') or []
    return (result.get('player') == identity_id and not result.get('upstream_version')
            and result.get('pending') == 0 and result.get('requests') == len(receipts) + 1
            and bool(receipts) and all(r.get('action') == 'status' and r.get('ok') is True
                                      and not r.get('resource') for r in receipts))


def read_only_browser_failure(record, identity_id):
    """Review old query/checkpoint failures; unresolved gameplay stays held."""
    browser = record.get('browser') or {}
    result = _browser_evidence(record)
    entry_loading = (browser.get('server_checked') is True and not browser.get('error')
                     and (result.get('report') or {}).get('error') ==
                     '游戏未接受郊外战斗入口，请确认人物已进入区域')
    if not entry_loading and browser.get('error') not in {'quest_transport_unconfirmed', 'quest_http_200_non_json'}:
        return False
    receipts = result.get('receipts') or []
    statuses = [row for row in receipts if row.get('resource') == 'quests' and row.get('action') == 'status']
    pending = result.get('pending_operations')
    harmless = lambda row: row.get('resource') == 'location' or (
        row.get('resource') == 'players/social' and row.get('action') == 'view')
    return (result.get('player') == identity_id and result.get('upstream_version') in SUPPORTED_UPSTREAM_VERSIONS
            and isinstance(pending, list) and result.get('pending') == len(pending)
            and all(harmless(row) for row in pending) and result.get('casts') == 0
            and result.get('requests') in {len(statuses), len(statuses) + 1} and bool(statuses)
            and all((row in statuses and row.get('ok') is True) or harmless(row) for row in receipts))


def native_flight_only_failure(record, identity_id):
    """A heartbeat-only hold still requires fresh server proof the flight ended."""
    browser = record.get('browser') or {}
    if browser.get('error') not in {'quest_transport_unconfirmed', 'quest_http_200_non_json'} or not browser.get('evidence_dir'):
        return False
    result = _browser_evidence(record)
    pending, receipts = result.get('pending_operations'), result.get('receipts')
    if (result.get('player') != identity_id or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS
            or result.get('casts') != 0 or not isinstance(pending, list) or not pending
            or result.get('pending') != len(pending) or not isinstance(receipts, list)
            or not all(isinstance(row, dict) for row in pending + receipts)):
        return False
    def cleanup(row):
        return row.get('resource') == 'location' or (
            row.get('resource') == 'flight' and row.get('action') in {'tick', 'stop'})
    statuses = [row for row in receipts if row.get('resource') == 'quests' and row.get('action') == 'status']
    return (bool(statuses) and result.get('requests') in {len(statuses), len(statuses) + 1}
            and all(cleanup(row) for row in pending)
            and any(row.get('resource') == 'flight' for row in pending)
            and all(cleanup(row) or (
                row.get('resource') == 'players/social' and row.get('action') == 'view') or (
                row.get('ok') is True and (row in statuses or (
                    row.get('resource') == 'flight' and row.get('action') in {'start', 'status'})))
                for row in receipts))


def server_flight_closed(response):
    return (isinstance(response, dict) and response.get('ok') is True
            and isinstance(response.get('mana'), dict) and response['mana'].get('flightLocked') is False)


def rate_limited_flight_start_only(record, identity_id):
    """Recognize one decoded takeoff rejection, never unknown flight/game writes."""
    result = _browser_evidence(record)
    pending, receipts = result.get('pending_operations'), result.get('receipts')
    observed, position = record.get('observed_at'), result.get('position')
    report = result.get('report')
    if ((record.get('browser') or {}).get('error') != 'quest_http_429_rate_limited'
            or result.get('error') != 'quest_http_429_rate_limited'
            or result.get('player') != identity_id or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS
            or result.get('transport_policy') != 1 or result.get('navigation_policy') != 4
            or result.get('phase') != 'held' or result.get('state') != 'ready'
            or (result.get('run') or {}).get('done') is not True
            or result.get('casts') != 0 or result.get('combat_active') is not False
            or any(result.get(key) for key in ('entry_error', 'status_error', 'cast_error',
                                             'browser_shutdown_error', 'browser_close_error', 'reward_receipts'))
            or result.get('cleanup_error') not in (None, '', 'TimeoutError')
            or (result.get('world') or {}).get('current') != 'yue'
            or not isinstance(position, list) or len(position) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)
            or math.hypot(position[0], position[2]) >= 300
            or not isinstance(report, dict) or report.get('error') or report.get('fieldSettlements') != []
            or type(observed) not in (int, float) or not math.isfinite(observed)
            or not 0 <= time.time() - observed < 60
            or not isinstance(pending, list) or len(pending) != 1 or result.get('pending') != 1
            or result.get('stored_pending') != pending or not isinstance(receipts, list)
            or not all(isinstance(row, dict) for row in pending + receipts)):
        return False
    row = pending[0]
    if (set(row) != {'resource', 'action', 'operation_id', 'at'} or row.get('resource') != 'flight'
            or row.get('action') != 'start' or not isinstance(row.get('operation_id'), str)
            or not row['operation_id'].startswith('flight:')
            or type(row.get('at')) not in (int, float) or not math.isfinite(row['at'])):
        return False
    statuses = [ack for ack in receipts if ack.get('resource') == 'quests' and ack.get('action') == 'status'
                and ack.get('ok') is True and ack.get('http_status') == 200 and not ack.get('error')
                and not ack.get('parse_error')]
    rejected = [ack for ack in receipts if ack.get('resource') == 'flight' and ack.get('action') == 'start'
                and ack.get('operation_id') == row['operation_id'] and ack.get('ok') is False
                and ack.get('http_status') == 429 and ack.get('error') == 'rate_limited'
                and not ack.get('parse_error') and str(ack.get('content_type', '')).startswith('application/json')]
    return (bool(statuses) and len(rejected) == 1 and len(receipts) == len(statuses) + 1
            and result.get('requests') == len(statuses)
            and all(not ack.get('grants') and not ack.get('cultivation') and not ack.get('pickedItems')
                    and type(ack.get('at')) in (int, float) and math.isfinite(ack['at'])
                    and ack['at'] <= observed for ack in receipts)
            and rejected[0]['at'] >= row['at']
            and any(ack['at'] > rejected[0]['at'] for ack in statuses))


def confirmed_cast_receipts(record, identity_id):
    """Only exact acknowledged uploads may clear a stale Chromium disk journal."""
    receipts = []
    for browser in (record.get('browser'), record.get('previous_browser')):
        result = _browser_evidence({'browser': browser or {}})
        if (result.get('player') != identity_id or result.get('server_checked') is not True
                or result.get('pending') != 0 or result.get('error')):
            continue
        receipts.extend({'operation_id': row['operation_id'], 'at': row['at']}
                        for row in result.get('receipts') or [] if isinstance(row, dict)
                        and row.get('resource') == 'field-cast' and row.get('action') == 'events'
                        and row.get('ok') is True and isinstance(row.get('operation_id'), str)
                        and type(row.get('at')) in (int, float) and math.isfinite(row['at']))
    return receipts


def combat_interrupted_navigation_stop(record, identity_id):
    """Upgrade only the old walking timer, after fresh review of a drained run."""
    result = _browser_evidence(record)
    report = result.get('report') or {}
    final = result.get('final_status') or {}
    world = result.get('world') or {}
    receipts = result.get('receipts')
    if (not all(isinstance(value, dict) for value in (report, final, world))
            or not isinstance(receipts, list)
            or not all(isinstance(row, dict) and row.get('ok') is True for row in receipts)):
        return False
    ended = final.get('at')
    observed = record.get('observed_at')
    position = result.get('position')
    settlements = report.get('fieldSettlements')
    return (result.get('player') == identity_id and result.get('upstream_version') in SUPPORTED_UPSTREAM_VERSIONS
            and result.get('navigation_policy') == 1 and result.get('phase') == 'held'
            and not result.get('error') and not result.get('status_error')
            and result.get('pending') == 0 and result.get('stored_pending') == []
            and result.get('combat_active') is False and world.get('current') == 'yue'
            and re.fullmatch(r'寻路超时：\[[^\]\n]+\]', report.get('error', '')) is not None
            and isinstance(position, list) and len(position) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in position)
            and math.hypot(position[0], position[2]) < 300
            and isinstance(settlements, list) and bool(settlements)
            and all(isinstance(row, dict) and row.get('ok') is True for row in settlements)
            and type(ended) in (int, float) and math.isfinite(ended)
            and type(observed) in (int, float) and math.isfinite(observed)
            and ended <= observed and 0 <= time.time() - observed < 60
            and any(isinstance(row, dict) and row.get('resource') == 'quests'
                    and row.get('action') == 'status' and row.get('ok') is True
                    and type(row.get('at')) in (int, float) and 0 <= ended - row['at'] < 2
                    for row in receipts))


def rejected_field_release_only(record, identity_id):
    """Resolve only exact native release rejections, never replay the release."""
    result = _browser_evidence(record)
    pending, receipts = result.get('pending_operations'), result.get('receipts')
    position, observed = result.get('position'), record.get('observed_at')
    if (result.get('player') != identity_id or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS
            or result.get('navigation_policy') != 2 or result.get('phase') != 'held'
            or result.get('error') != 'quest_http_409_quest_too_far' or result.get('status_error')
            or result.get('combat_active') is not False or (result.get('world') or {}).get('current') != 'yue'
            or not isinstance(position, list) or len(position) != 3
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in position)
            or math.hypot(position[0], position[2]) >= 300
            or type(observed) not in (int, float) or not math.isfinite(observed)
            or not 0 <= time.time() - observed < 60
            or not isinstance(pending, list) or not pending or result.get('pending') != len(pending)
            or result.get('stored_pending') != pending or not isinstance(receipts, list)
            or not all(isinstance(row, dict) for row in pending + receipts)):
        return False
    def rejected(row):
        return (row.get('resource') == 'quests' and row.get('action') == 'field-release'
                and row.get('ok') is False and row.get('http_status') == 409
                and row.get('error') == 'quest_too_far')
    return (all(row.get('ok') is True or rejected(row) for row in receipts)
            and all(row.get('resource') == 'quests' and row.get('action') == 'field-release'
                    and isinstance(row.get('operation_id'), str) and bool(row['operation_id'])
                    and type(row.get('at')) in (int, float) and math.isfinite(row['at'])
                    and any(rejected(ack) and ack.get('operation_id') == row['operation_id']
                            and ack.get('enemy_id') == row.get('enemy_id') and bool(row.get('enemy_id'))
                            and type(ack.get('at')) in (int, float) and math.isfinite(ack['at'])
                            and ack['at'] >= row['at'] for ack in receipts)
                    for row in pending))


def _previous_run_verified(record):
    previous = record.get('previous_browser') or {}
    return (previous.get('server_checked') is True and not previous.get('error')
            and previous.get('pending') == 0 and previous.get('flight_checked') is True)


def gateway_refused_cast_completion(record, identity_id):
    """A cast 'done' proof that only ever met the gateway's origin-down page.

    Cloudflare answers 521-523 itself when the origin is unreachable, so the
    upload was never processed. Its 'begin' and every engage and defeat before
    it were acknowledged. The proof is abandoned, never replayed.
    """
    result = _browser_evidence(record)
    pending, receipts = result.get('pending_operations'), result.get('receipts')
    report = result.get('report') or {}
    if (result.get('player') != identity_id or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS
            or result.get('transport_policy') != 1 or result.get('phase') != 'held'
            or result.get('error') != 'quest_cast_response_unreadable'
            or result.get('entry_error') or result.get('browser_shutdown_error') or result.get('browser_close_error')
            or not _previous_run_verified(record)
            or not isinstance(pending, list) or len(pending) != 1 or result.get('stored_pending') != pending
            or type(result.get('pending')) is not int or not 1 <= result['pending'] <= 2
            or not isinstance(receipts, list) or not all(isinstance(row, dict) for row in pending + receipts)
            or result.get('reward_receipts') or not isinstance(report, dict)
            or not isinstance(report.get('fieldSettlements'), list)
            or not all(isinstance(row, dict) and row.get('ok') is True for row in report['fieldSettlements'])):
        return False
    row = pending[0]
    events, operation = row.get('events'), row.get('operation_id')
    if (row.get('resource') != 'field-cast' or row.get('action') != 'events'
            or not isinstance(operation, str) or not operation.startswith('field-cast:done:')
            or type(row.get('at')) not in (int, float) or not math.isfinite(row['at'])
            or not isinstance(events, list) or len(events) != 1
            or not isinstance(events[0], dict) or events[0].get('k') != 'done'):
        return False
    begin = 'field-cast:begin:' + operation.removeprefix('field-cast:done:')
    failed = [r for r in receipts if r.get('ok') is not True]
    def refused(r):
        return (r.get('ok') is False and r.get('http_status') in {521, 522, 523}
                and str(r.get('content_type', '')).startswith('text/html')
                and not r.get('grants') and not r.get('pickedItems') and not r.get('cultivation')
                and ((r.get('resource') == 'field-cast' and r.get('action') == 'events'
                      and r.get('operation_id') == operation)
                     or (r.get('resource') == 'quests' and r.get('action') == 'status')))
    def aborted_read(r):
        return (r.get('ok') is False and r.get('http_status') is None and r.get('error') == 'request_aborted'
                and ((r.get('resource') == 'quests' and r.get('action') == 'status')
                     or r.get('resource') == 'battle/doufa-profile'))
    return (any(r.get('operation_id') == operation for r in failed)
            and all(refused(r) or aborted_read(r) for r in failed)
            and any(r.get('ok') is True and r.get('resource') == 'field-cast' and r.get('operation_id') == begin
                    and type(r.get('at')) in (int, float) and r['at'] <= row['at'] for r in receipts)
            and not any(r.get('ok') is True and type(r.get('at')) in (int, float) and r['at'] > row['at']
                        for r in receipts))


def old_unconfirmed_field_preparation(record, identity_id):
    """Recognize the old body-read failure before any kill or reward request.

    Native recoverFieldDeaths synchronizes the spawn and discards a local death
    without an engagement token. These uploads may be abandoned, never replayed.
    The read abort recurs on the upgraded transport. There it is admitted only
    after a cleanly verified run, so two failures in a row stay held.
    """
    result = _browser_evidence(record)
    pending, receipts = result.get('pending_operations'), result.get('receipts')
    report = result.get('report') or {}
    if (result.get('player') != identity_id or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS
            or result.get('transport_policy', 0) not in {0, 1} or result.get('navigation_policy') != 4
            or (result.get('transport_policy', 0) == 1 and not _previous_run_verified(record))
            or result.get('phase') != 'held' or result.get('error') != 'quest_http_200_non_json'
            or result.get('entry_error') or result.get('browser_shutdown_error') or result.get('browser_close_error')
            or type(result.get('casts')) is not int or result.get('casts') != 1
            or type(result.get('pending')) is not int or result.get('pending') != 4
            or not isinstance(pending, list) or len(pending) != 2 or result.get('stored_pending') != pending
            or not isinstance(receipts, list) or not all(isinstance(row, dict) for row in pending + receipts)
            or result.get('reward_receipts') or not isinstance(report, dict) or report.get('fieldSettlements') != []
            or not all(isinstance(r.get('resource'), str) and isinstance(r.get('action'), str) for r in pending)):
        return False
    statuses = [r for r in receipts if r.get('resource') == 'quests' and r.get('action') == 'status']
    if not statuses or result.get('requests') != len(statuses) + 1:
        return False
    expected = {('quests', 'field-engage'), ('field-cast', 'events')}
    if {(r.get('resource'), r.get('action')) for r in pending} != expected:
        return False
    for row in pending:
        if (not isinstance(row.get('operation_id'), str) or not row['operation_id']
                or type(row.get('at')) not in (int, float) or not math.isfinite(row['at'])):
            return False
        if row['resource'] == 'field-cast':
            events = row.get('events')
            if (row.get('attempts') != 1 or not isinstance(events, list) or len(events) != 1
                    or not isinstance(events[0], dict) or events[0].get('k') != 'use'):
                return False
        elif not isinstance(row.get('enemy_id'), str) or not row['enemy_id']:
            return False
        matched = [r for r in receipts if r.get('operation_id') == row['operation_id']]
        if (len(matched) != 1 or matched[0].get('resource') != row['resource']
                or matched[0].get('action') != row['action'] or matched[0].get('ok') is not False
                or matched[0].get('http_status') != 200 or matched[0].get('error') != 'non_json'
                or not str(matched[0].get('content_type', '')).startswith('application/json')
                or matched[0].get('parse_error', '') not in {'', 'AbortError'}
                or matched[0].get('enemy_id') != row.get('enemy_id')
                or type(matched[0].get('at')) not in (int, float)
                or not 0 <= matched[0]['at'] - row['at'] < 30):
            return False
    return all(not r.get('grants') and not r.get('pickedItems') and not r.get('cultivation') and (
        (r in statuses and r.get('ok') is True) or
        (r.get('resource') == 'flight' and r.get('action') == 'start' and r.get('ok') is True) or
        any(r.get('operation_id') == row['operation_id'] for row in pending)) for r in receipts)


def safe_browser_town_stop(result, identity_id):
    """A stopped worker in town, with no unresolved writes or cleanup errors."""
    report, world = result.get('report') or {}, result.get('world') or {}
    stop_reason = result.get('stop_reason') or {}
    if not all(isinstance(value, dict) for value in (report, world, stop_reason, result.get('final_status') or {})):
        return False
    if (result.get('player') != identity_id or result.get('phase') not in {'held', 'waiting'}
            or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS or result.get('error') or result.get('status_error')
            or result.get('cast_error') or result.get('pending') != 0 or result.get('stored_pending') != []
            or result.get('pending_operations') != []
            or result.get('combat_active') is not False or world.get('current') != 'yue'
            or result.get('entry_error') or result.get('snapshot_error') or result.get('browser_shutdown_error')
            or result.get('browser_close_error') or result.get('cleanup_error')
            or stop_reason.get('reason') not in {None, 'quest_request_expired_before_send'}
            or not isinstance(report.get('error'), str)):
        return False
    position = result.get('position')
    return (isinstance(position, list) and len(position) == 3
            and all(type(v) in (int, float) and math.isfinite(v) for v in position)
            and math.hypot(position[0], position[2]) < 300)


def safe_low_health_return(result, identity_id):
    return (safe_browser_town_stop(result, identity_id)
            and (result.get('report') or {}).get('error') ==
            '野外气血低于 30% 且无法立即回血，已中止任务并尝试安全回城'
            and type(result.get('hp')) in (int, float) and math.isfinite(result['hp']) and result['hp'] > 0)


def ready_npc_navigation_handoff(result, identity_id):
    """Hand a drained NPC approach back to the existing status-first protocol."""
    failure, states = result.get('navigation_failure') or {}, result.get('states') or {}
    if (not safe_browser_town_stop(result, identity_id)
            or not isinstance(failure, dict) or not isinstance(states, dict)
            or re.fullmatch(r'找不到可步行路线：\[[^\]\n]+\] · 可御空',
                            (result.get('report') or {}).get('error', '')) is None):
        return None
    goal = failure.get('goal')
    if (not isinstance(goal, list) or len(goal) != 3
            or not all(type(v) in (int, float) and math.isfinite(v) for v in goal)):
        return None
    npcs = {npc['id']: npc for npc in catalog()['npcs']}
    for quest in catalog()['quests']:
        slot = states.get(quest['id']) or {}
        npc = npcs.get(quest.get('receiver')) or {}
        point = _position(npc.get('positionXZ'))
        if (isinstance(slot, dict) and slot.get('status') == 'ready' and quest.get('region', 'yue') == 'yue'
                and point and math.hypot(goal[0] - point[0], goal[2] - point[2]) < .05
                and abs(goal[1] - point[1]) <= 2):
            return quest['id']
    return None


async def review_browser_hold(call, record, identity_id):
    """Called only after the owning identity's quest status was freshly read."""
    if not record.get('gameplay_uncertain'):
        return
    browser = record.get('browser') or {}
    if browser.get('workflow') == 'rewards':
        # An NPC/flight status read cannot reconcile nine independent reward
        # resources. Keep this batch held until its complete native status set
        # and pending journal are reviewed together.
        return
    reason = ''
    result = _browser_evidence(record)
    if result and isinstance(record.get('browser'), dict) and 'evidence' not in record['browser']:
        # The caller persists the record right after this review.
        record['browser']['evidence'] = evidence_digest(result)
    if (result.get('player') == identity_id and result.get('entry_error')
            and not result.get('mainline_started_at') and result.get('server_checked') is True
            and (result.get('final_status') or {}).get('fresh') is True
            and result.get('pending') == 0 and result.get('stored_pending') == []
            and result.get('casts') == 0 and result.get('combat_active') is False):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'verified_entry_failure'
    elif (result.get('player') == identity_id and result.get('upstream_version') in SUPPORTED_UPSTREAM_VERSIONS
          and result.get('navigation_policy', 0) in {0, 3}
          and (result.get('navigation_policy', 0) == 0 or (
              isinstance(result.get('navigation_failure'), dict)
              and result['navigation_failure'].get('obstaclesReviewed') is True
              and isinstance(result['navigation_failure'].get('recovery'), dict)
              and result['navigation_failure']['recovery'].get('needsTakeoffWalk') is True))
          and result.get('phase') == 'held' and result.get('server_checked') is True
          and (result.get('final_status') or {}).get('fresh') is True and not result.get('error')
          and result.get('pending') == 0 and result.get('stored_pending') == []
          and result.get('combat_active') is False
          and (result.get('world') or {}).get('current') == 'yue'
          and re.fullmatch(r'找不到可步行路线：\[[^\]\n]+\] · 先步行到可起飞的室外空地',
                           (result.get('report') or {}).get('error', ''))
          and isinstance(result.get('position'), list) and len(result['position']) == 3
          and all(type(v) in (int, float) and math.isfinite(v) for v in result['position'])
          and math.hypot(result['position'][0], result['position'][2]) < 300):
        # A single upgrade retry of the old navigator, after a proven safe return.
        # Only the fixed old policies may retry once after verified safe return.
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = ('native_takeoff_navigation_upgrade' if result.get('navigation_policy') == 3
                      else 'legacy_navigation_safe_return')
    elif combat_interrupted_navigation_stop(record, identity_id):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'combat_interrupted_navigation_upgrade'
    elif safe_low_health_return(result, identity_id):
        ended = (result.get('final_status') or {}).get('at')
        observed = record.get('observed_at')
        if (type(ended) in (int, float) and math.isfinite(ended)
                and type(observed) in (int, float) and math.isfinite(observed)
                and ended <= observed and 0 <= time.time() - observed < 60):
            flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
            if server_flight_closed(flight):
                reason = 'verified_low_health_safe_return'
    elif (result.get('player') == identity_id and result.get('phase') in {'done', 'bounded_stop'}
          and result.get('server_checked') is False and not result.get('error')
          and (result.get('final_status') or {}).get('fresh') is False
          and result.get('pending') == 0 and result.get('stored_pending') == []
          and (result.get('combat_active') is False or result.get('overworld') is True)
          and (result.get('report') or {}).get('error', '') in {'', '主线运行达到设置的时限'}):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'fresh_status_after_drained_browser_stop'
    elif (result.get('player') == identity_id and result.get('server_checked') is True
          and (result.get('final_status') or {}).get('fresh') is True and not result.get('error')
          and result.get('pending') == 0 and result.get('stored_pending') == []
          and result.get('combat_active') is False
          and (result.get('report') or {}).get('error') == '采集中发生位移，已中断'):
        gathers = [row for row in result.get('receipts', []) if row.get('resource') == 'quests'
                   and row.get('action') in {'gather-start', 'gather-cancel', 'gather-finish'}]
        if gathers and gathers[-1].get('action') == 'gather-cancel' and gathers[-1].get('ok') is True:
            flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
            if server_flight_closed(flight):
                reason = 'confirmed_gather_cancel_after_movement'
    elif rejected_field_release_only(record, identity_id):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'confirmed_field_release_rejection'
    elif (quest_id := ready_npc_navigation_handoff(result, identity_id)) and (
            (record.get('observed') or {}).get(quest_id, {}).get('status') in {'ready', 'rewarded'}):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'verified_npc_delivery_handoff'
    elif old_unconfirmed_field_preparation(record, identity_id):
        fresh = await call('status')
        state = fresh.get('quests') or {}
        field = state.get('field') if isinstance(state, dict) else None
        enemies = field.get('enemies') if isinstance(field, dict) else None
        enemy_id = next(r['enemy_id'] for r in result['pending_operations'] if r['resource'] == 'quests')
        if (fresh.get('ok') is True and str(fresh.get('playerId', identity_id)) == str(identity_id)
                and isinstance(state, dict) and isinstance(state.get('states'), dict)
                and state.get('corpses') == [] and isinstance(enemies, list)
                and any(r.get('id') == enemy_id and type(r.get('generation')) is int
                        and type(r.get('hp')) in (int, float) and r['hp'] > 0
                        for r in enemies if isinstance(r, dict))):
            flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
            if server_flight_closed(flight):
                reason = 'unconfirmed_field_preparation_upgrade'
    elif gateway_refused_cast_completion(record, identity_id):
        observed = record.get('observed_at')
        if (type(observed) in (int, float) and math.isfinite(observed) and 0 <= time.time() - observed < 60):
            flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
            if server_flight_closed(flight):
                reason = 'gateway_refused_cast_completion'
    elif legacy_cast_only(record, identity_id):
        reason = 'legacy_cast_only_no_quest_mutations'
    elif read_only_browser_failure(record, identity_id):
        reason = 'read_only_failure_no_mutations'
    elif rate_limited_flight_start_only(record, identity_id):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'rate_limited_flight_start_closed'
    elif native_flight_only_failure(record, identity_id):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'native_flight_session_closed'
    elif orphan_cast_proof_only(record, identity_id):
        # A proof upload alone cannot engage/defeat an enemy or grant rewards.
        # Retain it as abandoned evidence; never reconstruct or replay the lost
        # events. Fresh quest status has already been read by the caller.
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'orphan_cast_no_engagement'
    elif (record.get('last_error') == 'qingxi_flight_requires_review'
          and browser.get('server_checked') is True and not browser.get('error')):
        flight = await call('status', _api_path='/api/miniapp/xianxia-dwelling/flight')
        if server_flight_closed(flight):
            reason = 'confirmed_browser_flight_closed'
    elif browser.get('server_checked') is True and browser.get('error') in {
            'quest_progress_stalled', 'quest_movement_not_ready', 'KeyboardInterrupt'}:
        reason = 'server_checked_safe_stop'
    if reason:
        rollback = copy.deepcopy(record) if reason == 'rate_limited_flight_start_closed' else None
        record.update(gameplay_uncertain=False, phase='held' if record.get('pending') or record.get('gather')
                      else 'waiting_gameplay', last_error='', next_at=time.time() + 60)
        record['browser_review'] = {'at': time.time(), 'evidence_dir': browser.get('evidence_dir'), 'reason': reason}
        if reason in {'orphan_cast_no_engagement', 'gateway_refused_cast_completion'}:
            record['browser_review']['abandoned_cast_pending'] = _browser_evidence(record)['pending_operations']
        if reason == 'confirmed_field_release_rejection':
            record['browser_review']['rejected_release_pending'] = result['pending_operations']
        if reason == 'unconfirmed_field_preparation_upgrade':
            record['browser_review']['abandoned_preparation_pending'] = result['pending_operations']
        if reason == 'rate_limited_flight_start_closed':
            record['browser_review']['rejected_flight_pending'] = result['pending_operations']
        try:
            save()
        except Exception:
            if rollback is not None:
                record.clear()
                record.update(rollback)
            raise


def orphan_cast_proof_only(record, identity_id):
    """Recognize the old first-upload failure, excluding every gameplay write."""
    browser = record.get('browser') or {}
    if browser.get('error') != 'quest_http_200_non_json':
        return False
    result = _browser_evidence(record)
    pending, receipts = result.get('pending_operations'), result.get('receipts')
    if (result.get('player') != identity_id or result.get('upstream_version') not in SUPPORTED_UPSTREAM_VERSIONS
            or result.get('casts') != 1 or result.get('pending') != 1
            or not isinstance(pending, list) or len(pending) != 1
            or not isinstance(pending[0], dict) or pending[0].get('resource') != 'field-cast'
            or pending[0].get('action') != 'events' or not pending[0].get('operation_id')
            or not isinstance(receipts, list) or not all(isinstance(row, dict) for row in receipts)
            or result.get('reward_receipts') or (result.get('report') or {}).get('fieldSettlements')):
        return False
    statuses = [row for row in receipts if row.get('resource') == 'quests' and row.get('action') == 'status']
    casts = [row for row in receipts if row.get('resource') == 'field-cast']
    return (bool(statuses) and result.get('requests') == len(statuses) and len(casts) == 1
            and casts[0].get('operation_id') == pending[0]['operation_id']
            and all(row in casts or (row in statuses and row.get('ok') is True) or (
                row.get('resource') == 'flight' and (row.get('action') == 'status' or (
                    row.get('action') == 'start' and row.get('ok') is True))) for row in receipts))


def summarize(data, rules):
    known = {q['id']: q for q in rules['quests']}
    return {key: {'status': slot.get('status'), 'progress': copy.deepcopy(slot.get('progress') or {}),
                  'required': copy.deepcopy(slot.get('required') or {}), 'title': known.get(key, {}).get('title', key)}
            for key, slot in data.get('states', {}).items() if isinstance(slot, dict)}


def field_status_evidence(data, record, *, verified_player_id, observed_at):
    """Bounded diagnostics from an existing status read; never release evidence."""
    unknown = 'unknown'
    def identifier(value):
        return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_:.-]{1,100}', value) else unknown
    def number(value, *, integer=False):
        if type(value) is int if integer else type(value) in (int, float):
            try:
                if math.isfinite(value) and value >= 0:
                    return value
            except OverflowError:
                pass
        return unknown
    def active(row):
        return row['active'] if isinstance(row, dict) and type(row.get('active')) is bool else unknown
    def engagement(row):
        if not isinstance(row, dict) or 'engagement' not in row:
            return unknown
        value = row['engagement']
        if value is None or type(value) is bool:
            return value
        if isinstance(value, str):
            return {'present': True, 'type': 'string'}  # Never persist a bearer/engagement token.
        if isinstance(value, dict):
            return {'present': True, 'type': 'object', 'active': active(value),
                    'enemy_id': identifier(value.get('enemyId')),
                    'generation': number(value.get('generation'), integer=True),
                    'expires_at': number(value.get('expiresAt'))}
        return unknown
    browser = record.get('browser') or {}
    result = _browser_evidence(record)
    pending = result.get('pending_operations')
    source = 'browser_result'
    if not isinstance(pending, list):
        pending = (browser.get('evidence') or {}).get('pending_operations')
        source = 'browser_digest' if isinstance(pending, list) else unknown
    rows = [*pending] if isinstance(pending, list) else []
    if isinstance(record.get('pending'), dict):
        source = source + '+host_pending' if rows and source != unknown else 'host_pending'
        rows.append(record['pending'])
    target_ids = sorted({value for row in rows if isinstance(row, dict)
                         for value in [identifier(row.get('enemy_id') or row.get('enemyId'))] if value != unknown})
    field = data.get('field') if isinstance(data, dict) else None
    enemies = field.get('enemies') if isinstance(field, dict) else None
    corpses = data.get('corpses') if isinstance(data, dict) else None
    roster_known = (isinstance(enemies, list) and all(isinstance(row, dict)
                    and identifier(row.get('id')) != unknown for row in enemies))
    corpses_known = (isinstance(corpses, list) and all(isinstance(row, dict)
                     and identifier(row.get('id')) != unknown and identifier(row.get('enemyId')) != unknown
                     for row in corpses))
    targets = []
    for target in target_ids[:8]:
        matches = [row for row in enemies if isinstance(row, dict) and row.get('id') == target] if isinstance(enemies, list) else []
        enemy = matches[0] if len(matches) == 1 else {}
        refs = [row for row in corpses if isinstance(row, dict) and row.get('enemyId') == target] if isinstance(corpses, list) else []
        targets.append({'enemy_id': target,
            'roster_state': 'present' if len(matches) == 1 else 'duplicate' if matches else 'absent' if roster_known else unknown,
            'generation': number(enemy.get('generation'), integer=True), 'hp': number(enemy.get('hp')),
            'respawn_at': number(enemy.get('respawnAt')), 'active': active(enemy), 'engagement': engagement(enemy),
            'corpses_state': 'known' if corpses_known else unknown,
            'corpses': [{'id': identifier(row.get('id')), 'enemy_id': target,
                        'generation': number(row.get('generation'), integer=True),
                        'expires_at': number(row.get('expiresAt')),
                        'looted': row['looted'] if type(row.get('looted')) is bool else unknown} for row in refs[:8]],
            'corpses_truncated': len(refs) > 8})
    return {'verified_player_id': verified_player_id if type(verified_player_id) is int and verified_player_id > 0 else unknown,
            'observed_at': number(observed_at), 'pending_source': source,
            'field_active': active(field), 'field_engagement': engagement(field),
            'targets': targets, 'targets_truncated': len(target_ids) > 8}


async def run_protocol(call, record, persist, *, rules=None, choices=None, status_only=False,
                       sleeper=asyncio.sleep, max_actions=64):
    rules = rules or catalog()
    response = await call('status')
    def observe(reply):
        if (not isinstance(reply, dict) or reply.get('ok') is not True
                or not isinstance(reply.get('quests'), dict)
                or not isinstance(reply['quests'].get('states'), dict)):
            raise RuntimeError('qingxi_receipt_missing')
        record.update(observed=summarize(reply['quests'], rules), observed_at=time.time())
        record['available_nodes'] = [{k: n[k] for k in ('id', 'itemId', 'position', 'region', 'puzzle', 'aerial') if k in n}
                                     for n in reply['quests'].get('nodes', [])][:48]
        persist()
        return reply['quests']
    data = observe(response)
    pending = record.get('pending') or {}
    state = data['states'].get(pending.get('questId'), {}).get('status')
    confirmed = (pending.get('action') == 'turn-in' and state == 'rewarded'
                 or pending.get('action') == 'accept' and state in {'active', 'ready', 'rewarded'})
    if confirmed:
        record.setdefault('receipts', []).append({'operation_id': pending['operationId'],
            'action': pending['action'], 'quest_id': pending['questId'], 'at': time.time(),
            'reconciled_from_status': True, 'grants': []})
        record.pop('pending')
        record.update(phase='held' if record.get('gameplay_uncertain') or record.get('gather') else 'idle', last_error='')
        persist()
    if status_only:
        return record
    if record.get('pending'):
        raise RuntimeError('qingxi_pending_requires_review')

    async def mutate(payload):
        action = payload['action']
        pending = {**payload, 'operationId': 'qx-' + uuid.uuid4().hex}
        record.update(pending=pending, phase='running')
        persist()  # A mutation cannot leave the host before its intent is durable.
        reply = await call(action, **{k: v for k, v in pending.items() if k != 'action'})
        next_data = observe(reply)
        if action in {'accept', 'turn-in'}:
            status = next_data['states'].get(payload['questId'], {}).get('status')
            expected = {'active', 'ready'} if action == 'accept' else {'rewarded'}
            if status not in expected:
                raise RuntimeError('qingxi_mutation_receipt_invalid')
        if action == 'gather-start' and (not reply.get('gatherId')
                or type(reply.get('duration')) not in (int, float)
                or not math.isfinite(reply['duration']) or not 0 < reply['duration'] <= 60):
            raise RuntimeError('qingxi_gather_receipt_invalid')
        if action == 'gather-start':
            record['gather'] = {'gatherId': reply['gatherId'], 'duration': reply['duration'], **payload}
        record.setdefault('receipts', []).append({'operation_id': pending['operationId'], 'action': action,
            'quest_id': payload.get('questId'), 'node_id': payload.get('nodeId'), 'at': time.time(),
            'grants': copy.deepcopy(reply.get('grants') or []), 'reused': bool(reply.get('reused'))})
        record['receipts'] = record['receipts'][-160:]
        record.pop('pending')
        try:
            persist()
        except BaseException:
            record.update(pending=pending, phase='held')
            raise
        return reply, next_data

    seen = set()
    for _ in range(max_actions):
        action, gaps = plan_next(data, rules, choices)
        record['gaps'] = gaps
        if action is None:
            record.update(phase='waiting_gameplay' if gaps else 'done', next_at=time.time() + 86400)
            persist()
            return record
        fingerprint = json.dumps([action, record['observed']], sort_keys=True)
        if fingerprint in seen:
            raise RuntimeError('qingxi_no_progress')
        seen.add(fingerprint)
        reply, data = await mutate(action)
        if action['action'] == 'gather-start':
            record['gather'] = {'gatherId': reply['gatherId'], 'duration': reply['duration'], **action}
            persist()
            await sleeper(reply['duration'] + .5)
            _, data = await mutate({**action, 'action': 'gather-finish', 'gatherId': reply['gatherId']})
            record.pop('gather', None)
            persist()
    record.update(phase='idle', next_at=time.time() + 1800)
    persist()
    return record


def reward_batch_verified(result, identity_id):
    """Check the complete final native status set before releasing scene ownership."""
    batch = result.get('reward_batch')
    if not isinstance(batch, dict) or batch.get('phase') not in {'complete', 'partial'} or batch.get('running') is not False:
        return False
    final, before = batch.get('final'), batch.get('before')
    if not isinstance(final, dict) or not isinstance(before, dict):
        return False
    if (batch.get('error') or final.get('verified') is not True or final.get('player') != identity_id
            or final.get('day') != before.get('day')
            or not isinstance(final.get('day'), str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', final['day'])):
        return False
    daily = final.get('daily')
    ids = {'moonpalace', 'bloodland', 'southgate', 'escort', 'mulanshrine', 'bqpuppet', 'jianzhong'}
    if (not isinstance(daily, list) or len(daily) != 7
            or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in daily)):
        return False
    if {row.get('id') for row in daily} != ids or any(
            row.get('day') != final['day'] or type(row.get('claimed')) is not bool or type(row.get('hasKey')) is not bool
            or 'activeRun' not in row or row['activeRun'] is not None for row in daily):
        return False
    trials = final.get('trials')
    if not isinstance(trials, dict) or set(trials) != {'longwind', 'bamboo'}:
        return False
    for arena, status in trials.items():
        if (not isinstance(status, dict) or status.get('arena') != arena or status.get('day') != final['day']
                or status.get('limit') != 3 or type(status.get('remaining')) is not int or type(status.get('used')) is not int
                or status['remaining'] < 0 or status['used'] < 0 or status['remaining'] + status['used'] != 3
                or 'activeRun' not in status or status['activeRun'] is not None):
            return False
    return True


async def run_once(identity_id, *, status_only=False, reward_batch=False):
    from . import cave_treasure_runtime as cave
    owner = MiniAppIdentityOwner.capture(identity_id)
    if (owner is None or not get_global_enabled() or not is_cave_public_identity_available(identity_id)
            or get_activity_block_reason(identity_id, include_qingxi=False)):
        return False, '身份不可用或正在参加其他活动'
    if reward_batch and owner.account_id != identity_id:
        return False, '每日副本和试炼仅支持本人账号'
    lock = cave._public_entry_lock(identity_id)
    if lock.locked():
        return False, '洞府公共入口操作执行中'
    entry = _entry_url()
    config = get_expedition_config().get('qingxi_quests', {})
    reward_config = copy.deepcopy(get_expedition_config().get('qingxi_rewards', {}))
    record = records().setdefault(str(identity_id), {})
    if (reward_batch and status_only and record.get('gameplay_uncertain')
            and (record.get('browser') or {}).get('workflow') != 'rewards'):
        return False, '旧青溪任务仍待原任务证据核对，不能用奖励状态解除占用'
    if not status_only and (record.get('gather') or record.get('gameplay_uncertain')
            or (reward_batch and (record.get('pending') or record.get('phase') in {'held', 'running', 'loading'}))):
        return False, '青溪采集旧操作尚待核对'
    def allowed():
        return (owner.is_current() and get_global_enabled() and is_cave_public_identity_available(identity_id)
                and entry == _entry_url() and cave._public_entry_allowed()
                and config == get_expedition_config().get('qingxi_quests', {})
                and (not reward_batch or reward_config == get_expedition_config().get('qingxi_rewards', {}))
                and not get_activity_block_reason(identity_id, include_qingxi=False)
                and records().get(str(identity_id)) is record)
    async with lock:
        try:
            if not status_only:
                record.update(phase='loading', identity_id=identity_id, started_at=time.time(),
                              choices_seen=copy.deepcopy(config.get('choices') or {}))
                save()
            token, url, error = cave._parse_public_cave_entry_url(entry)
            if error:
                raise RuntimeError('qingxi_entry_unavailable')
            session = await cave._load_cave_public_identity_session(identity_id, token, url, now=time.time(),
                capture_source=f'qingxi_quests:{identity_id}', include_details=False, operation_check=allowed)
            if not session.get('ok') or str(session.get('player_id')) != str(identity_id):
                raise RuntimeError('qingxi_identity_unverified')
            adapter = build_cave_treasure_miniapp_adapter()
            transport = build_pooled_miniapp_transport(adapter_key='qingxi_quests', identity_id=identity_id, operation_check=allowed)
            budget = MiniAppRequestBudget(MiniAppRequestPolicy(min_interval_sec=5, max_requests_per_run=130, max_attempts_per_request=1))
            async def call(action, *, _api_path=API_PATH, **payload):
                require_miniapp_operation(allowed)
                request = build_miniapp_http_request(adapter, _api_path,
                    {'token': token, 'playerId': session['player_id'], 'action': action, **payload}, init_data=session['init_data'])
                result = await run_miniapp_blocking_flow(lambda operation: execute_miniapp_http_request(
                    request, transport, request_budget=budget, operation_check=operation.check, sleeper=operation.sleep,
                    capture_sink=cave._capture_store(time.time()), capture_source=f'qingxi_quests:{identity_id}', step_key=action),
                    operation_check=allowed)
                require_miniapp_operation(allowed)
                if not result.ok or not isinstance(result.data, dict):
                    raise RuntimeError('qingxi_request_unconfirmed')
                if result.data.get('playerId') is not None and str(result.data['playerId']) != str(identity_id):
                    raise RuntimeError('qingxi_player_mismatch')
                if (_api_path == API_PATH and action == 'status' and result.data.get('ok') is True
                        and isinstance(result.data.get('quests'), dict)
                        and isinstance(result.data['quests'].get('states'), dict)):
                    record['field_status_evidence'] = field_status_evidence(result.data['quests'], record,
                        verified_player_id=identity_id, observed_at=time.time())
                return result.data
            if reward_batch:
                await run_gameplay(owner, token, url, record, allowed, reward_batch=True, inspection_only=status_only,
                    verify_flight=lambda: call('status', _api_path='/api/miniapp/xianxia-dwelling/flight'))
                record.update(phase='idle', last_error='')
                if status_only:
                    record['reward_inspection'] = copy.deepcopy(record['browser']['reward_inspection'])
                    record['rewards_reviewed_at'] = time.time()
                    record['rewards_next_at'] = time.time() + 1800
                    save()
                    return True, '九项奖励、未决记录与飞行状态已只读核对'
                record['reward_batch'] = copy.deepcopy(record['browser']['reward_batch'])
                batch = record['reward_batch']
                from .changfeng_trial import _next_day
                record['rewards_next_at'] = (_next_day(time.time(), identity_id) if batch.get('remaining') == 0
                                             else time.time() + 1800)
                save()
                await notify_rewards(identity_id, record)
                return True, '青溪每日七副本与双试炼本轮已核对'
            await run_protocol(call, record, save, choices=config.get('choices'), status_only=status_only)
            if status_only:
                await review_browser_hold(call, record, identity_id)
                record.update(last_status_error='', reviewed_at=time.time(), review_next_at=time.time() + 1800)
                save()
            else:
                await notify_rewards(identity_id, record)
            if not status_only and browser_required(record.get('gaps', [])) and config.get('gameplay_enabled', False):
                await run_gameplay(owner, token, url, record, allowed, choices=config.get('choices'),
                    verify_flight=lambda: call('status', _api_path='/api/miniapp/xianxia-dwelling/flight'))
                await run_protocol(call, record, save, choices=config.get('choices'))
                if record.get('phase') == 'waiting_gameplay' and browser_required(record.get('gaps', [])):
                    reason = (record.get('browser', {}).get('stop_reason') or {}).get('reason')
                    from .changfeng_trial import _next_day
                    record['next_at'] = (_next_day(time.time(), identity_id) if reason in {
                        'daily_reward_full', 'daily_instance_locked'} else time.time() + 1800)
                    save()
            if not status_only:
                await notify_rewards(identity_id, record)
            return True, '青溪任务状态已核对' if status_only else '青溪任务本轮已结束'
        except (Exception, asyncio.CancelledError) as exc:
            code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            if reward_batch:
                record['rewards_next_at'] = time.time() + 1800
            if status_only:
                record['last_status_error'] = code
            else:
                record.update(phase='held' if record.get('pending') or record.get('gather') or record.get('gameplay_uncertain') or code == 'qingxi_no_progress' else 'idle',
                              last_error=code, next_at=time.time() + 1800)
            save()
            if not status_only:
                await notify_rewards(identity_id, record)
            if isinstance(exc, asyncio.CancelledError):
                raise
            return False, code


async def _drain_browser_process(process, owner_token):
    from .fanren_client_browser import _stop_native_process_group
    await _stop_native_process_group(process, owner_token)


async def run_gameplay(owner, token, url, record, allowed, *, verify_flight, choices=None, reward_batch=False,
                       inspection_only=False):
    from .fanren_client_browser import _PROCESS_OWNER_ENV, _await_uncancelled
    from .qingxi_trial_browser import TOOL_ROOT
    from .cave_treasure_miniapp import request_cave_treasure_miniapp_init_data
    script = Path(__file__).resolve().parents[2] / 'tools/qingxi_quest_browser.py'
    python = TOOL_ROOT / '.venv/bin/python'
    if not python.is_file() or not (TOOL_ROOT / 'tools/longwind_trial_bot.py').is_file():
        raise RuntimeError('qingxi_browser_dependency_missing')
    directory = TOOL_ROOT / '.runtime' / str(owner.identity_id) / ('quest-' + uuid.uuid4().hex)
    directory.mkdir(parents=True, mode=0o700)
    launch = directory / 'launch.url'
    options = directory / 'options.json'
    process = waiter = spawn_task = cleanup_task = None
    owner_token = uuid.uuid4().hex

    async def stop():
        nonlocal process, cleanup_task
        cancelled = False
        if process is None and spawn_task is not None:
            process, cancelled = await _await_uncancelled(spawn_task)
        if process is not None:
            if cleanup_task is None:
                cleanup_task = asyncio.create_task(_drain_browser_process(process, owner_token))
            _, interrupted = await _await_uncancelled(cleanup_task)
            cancelled = cancelled or interrupted
        if cancelled:
            raise asyncio.CancelledError

    try:
        launch_url = await request_cave_treasure_miniapp_init_data(owner.identity_id, token=token,
            webview_url=url, operation_check=allowed, return_webview_url=True)
        require_miniapp_operation(allowed)
        with os.fdopen(os.open(launch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            stream.write(launch_url)
        with os.fdopen(os.open(options, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            review = record.get('browser_review') or {}
            json.dump({'choices': choices or {}, 'reviewed_cast_pending':
                       review.get('abandoned_cast_pending', []) if review.get('reason') in {
                           'orphan_cast_no_engagement', 'gateway_refused_cast_completion'} else [],
                       'reviewed_release_pending': review.get('rejected_release_pending', [])
                       if review.get('reason') == 'confirmed_field_release_rejection' else [],
                       'reviewed_preparation_pending': review.get('abandoned_preparation_pending', [])
                       if review.get('reason') == 'unconfirmed_field_preparation_upgrade' else [],
                       'reviewed_flight_pending': review.get('rejected_flight_pending', [])
                       if (review.get('reason') == 'rate_limited_flight_start_closed'
                           and review.get('evidence_dir') == (record.get('browser') or {}).get('evidence_dir')) else [],
                       'confirmed_cast_receipts': confirmed_cast_receipts(record, owner.identity_id)}, stream)
        if record.get('browser'):
            record['previous_browser'] = copy.deepcopy(record['browser'])
        deadline_seconds = 11100 if reward_batch and not inspection_only else 1500
        record.update(phase='running', deadline=time.time() + deadline_seconds, gameplay_uncertain=True,
                      browser={'phase': 'running', 'evidence_dir': str(directory), 'version': '2.0.78',
                               'workflow': 'rewards' if reward_batch else 'quests'})
        save()
        command = [str(python), str(script), '--tool-root', str(TOOL_ROOT), '--url-file', str(launch),
                   '--profile-dir', str(directory.parent / 'profile'), '--result', str(directory / 'result.json'),
                   '--expected-player-id', str(owner.identity_id), '--options-file', str(options), '--max-seconds', '900']
        if reward_batch:
            command[-1] = '900' if inspection_only else '10800'
            command.extend(['--workflow', 'rewards'])
            if inspection_only:
                command.append('--read-only')
        with (directory / 'runner.log').open('w') as output:
            spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(
                *command, stdout=output, stderr=asyncio.subprocess.STDOUT, start_new_session=True,
                env={**os.environ, _PROCESS_OWNER_ENV: owner_token}))
            process = await asyncio.shield(spawn_task)
            waiter = asyncio.create_task(process.wait())
            deadline = time.monotonic() + deadline_seconds
            while process.returncode is None:
                if not allowed() or time.monotonic() >= deadline:
                    await stop()
                    break
                try:
                    await asyncio.wait_for(asyncio.shield(waiter), 5)
                except asyncio.TimeoutError:
                    pass
                live_path = directory / 'result.json'
                if reward_batch and live_path.is_file() and live_path.stat().st_size <= 2_000_000:
                    try:
                        live = json.loads(live_path.read_text())
                        if live.get('player') == owner.identity_id:
                            record['browser']['reward_batch'] = live.get('reward_batch')
                            record['browser']['report'] = live.get('report')
                            mark_dirty()
                    except (OSError, ValueError):
                        pass
        result = json.loads((directory / 'result.json').read_text())
        record['browser'].update({k: result.get(k) for k in (
            'phase', 'server_checked', 'error', 'entry_error', 'cleanup_error', 'status_error',
            'pending', 'final_status', 'requests', 'casts', 'site', 'stop_reason', 'report', 'reward_batch', 'reward_inspection')})
        record['browser']['evidence'] = evidence_digest(result)
        existing = {row.get('reward_id', row['operation_id']) for row in record.get('receipts', [])}
        for row in result.get('reward_receipts', []):
            reward_id = row.get('reward_id', row.get('operation_id'))
            if row.get('ok') is True and reward_id and reward_id not in existing:
                record.setdefault('receipts', []).append(row)
                existing.add(reward_id)
        save()
        confirmed_stop = ((result.get('server_checked') is True and (
                              process.returncode == 0 or safe_low_health_return(result, owner.identity_id)))
                          or ready_npc_navigation_handoff(result, owner.identity_id))
        if not confirmed_stop or result.get('player') != owner.identity_id:
            raise RuntimeError('qingxi_browser_requires_review')
        if reward_batch and (result.get('server_checked') is not True or result.get('pending') != 0
                or result.get('stored_pending') != [] or any(result.get(key) for key in (
                    'error', 'status_error', 'cleanup_error', 'browser_shutdown_error'))):
            raise RuntimeError('qingxi_reward_batch_requires_review')
        verification = result
        if inspection_only:
            inspection = result.get('reward_inspection') or {}
            verification = {'reward_batch': {'phase': 'partial', 'running': False,
                            'before': {'day': inspection.get('day')}, 'final': inspection}}
        if reward_batch and not reward_batch_verified(verification, owner.identity_id):
            raise RuntimeError('qingxi_reward_batch_requires_review')
        try:
            flight = await verify_flight()
        except Exception:
            raise RuntimeError('qingxi_flight_requires_review') from None
        if not server_flight_closed(flight):
            raise RuntimeError('qingxi_flight_requires_review')
        record['browser'].update(flight_checked=True, flight_checked_at=time.time())
        record['gameplay_uncertain'] = False
        save()
    finally:
        try:
            await stop()
        except BaseException as error:
            record.update(gameplay_uncertain=True, phase='held')
            record['browser']['cleanup_error'] = type(error).__name__
            save()
            raise
        finally:
            launch.unlink(missing_ok=True)


async def action(payload):
    kind = payload.get('action', 'status')
    if kind == 'status':
        return True, '青溪任务记录', {'qingxi_quests': copy.deepcopy(records())}
    if kind not in {'run', 'refresh', 'rewards', 'rewards_refresh'}:
        return False, '未知操作', {}
    try:
        identity_id = int(payload.get('send_as_id'))
    except (ValueError, TypeError):
        return False, '需要有效身份', {}
    if _TASKS:
        return False, '青溪任务操作执行中', {}
    task = track_background_task(asyncio.create_task(run_once(
        identity_id, status_only=kind in {'refresh', 'rewards_refresh'}, reward_batch=kind in {'rewards', 'rewards_refresh'})))
    _TASKS[identity_id] = task
    def done(completed):
        _TASKS.pop(identity_id, None)
        if not completed.cancelled() and completed.exception():
            print('Qingxi quests failed:', type(completed.exception()).__name__)
    task.add_done_callback(done)
    return True, '已安排青溪任务操作', {'qingxi_quests': copy.deepcopy(records())}


async def scheduler(now):
    config = get_expedition_config().get('qingxi_quests', {})
    if not config.get('enabled') or not get_global_enabled() or _TASKS:
        return
    for identity_id in config.get('participant_identity_ids', []):
        record = records().get(str(identity_id), {})
        if record.get('phase') in {'running', 'loading'}:
            continue
        review = record.get('phase') == 'held' or any(record.get(key) for key in (
            'pending', 'gather', 'gameplay_uncertain'))
        if review:
            due = record.get('review_next_at', record.get('next_at', 0))
            if type(due) not in (int, float) or not math.isfinite(due) or due > now:
                continue
            if (not is_cave_public_identity_available(identity_id)
                    or get_activity_block_reason(identity_id, include_qingxi=False)):
                continue
            from . import cave_treasure_runtime as cave
            if cave._public_entry_lock(identity_id).locked():
                continue
            record['review_next_at'] = now + 1800
            save()  # Bound failed/blocked reviews too; never retry on each scheduler tick.
            await action({'action': 'refresh', 'send_as_id': identity_id})
            return
        due = record.get('next_at', 0)
        choices_changed = record.get('choices_seen') != (config.get('choices') or {})
        if type(due) not in (int, float) or not math.isfinite(due) or (due > now and not choices_changed):
            continue
        if not is_cave_public_identity_available(identity_id) or get_activity_block_reason(identity_id):
            continue
        from . import cave_treasure_runtime as cave
        if cave._public_entry_lock(identity_id).locked():
            continue
        await action({'action': 'run', 'send_as_id': identity_id})
        return


def reset_on_startup():
    for record in records().values():
        if record.get('phase') in {'running', 'loading'}:
            if (record.get('browser') or {}).get('phase') == 'running':
                record['gameplay_uncertain'] = True
            record.update(phase='held' if record.get('pending') or record.get('gather') else 'idle',
                          last_error='程序重启，依据服务端任务状态恢复')
            if record.get('gameplay_uncertain'):
                record['phase'] = 'held'
            mark_dirty()
