"""Daily scheduling and visible server-owned progress for Qingxi's 7+6 slots."""
import copy
import math
import time

from ..activity_occupancy import get_activity_block_reason
from ..qingxi_single_daily import defeated_daily_today, valid_daily_id
from ..runtime import send_audit_log
from ..state import get_expedition_config, get_global_enabled, is_cave_public_identity_available
from .changfeng_trial import _day, _entry_url, _next_day

DAILY = {
    'moonpalace': '月殿血誓', 'bloodland': '血色禁地', 'southgate': '南门守城', 'escort': '护民出关',
    'mulanshrine': '血祭神坛', 'bqpuppet': '试傀场', 'jianzhong': '剑冢',
}
ARENAS = {'longwind': '麦田试炼', 'bamboo': '竹林试炼'}


def owns_identity(identity_id):
    config = get_expedition_config().get('qingxi_rewards', {})
    return config.get('enabled') is True and identity_id in config.get('participant_identity_ids', [])


def defeated_today(identity_id, now):
    from . import changfeng_trial as trial
    from . import qingxi_quests as quests
    from .qingxi_trial_browser import confirmed_defeat
    if defeated_daily_today(quests.records().get(str(identity_id), {}) or {}, _day(now)):
        return True
    record = trial._records().get(str(identity_id), {}) or {}
    if (record.get('auto_pause') or {}).get('reason') == 'confirmed_defeat':
        return True
    status = record.get('browser_trial') or {}
    finished = status.get('finished_at')
    return (confirmed_defeat(status) and type(finished) in (int, float)
            and math.isfinite(finished) and _day(finished) == _day(now))


def progress(record):
    """Consumption is visible but is never presented as a verified victory."""
    browser = record.get('browser') or {}
    batch = browser.get('reward_batch') or record.get('reward_batch') or {}
    observations = [row for row in (batch.get('final'), batch.get('before'), browser.get('reward_inspection'),
                                    record.get('reward_inspection')) if isinstance(row, dict)]
    def checked_at(row):
        value = row.get('checked_at')
        return value if type(value) in (int, float) and math.isfinite(value) else 0
    observation = max(observations, key=checked_at, default={})
    rows = {row.get('id'): row for row in observation.get('daily', []) if isinstance(row, dict)}
    phase = (record['phase'] if record.get('phase') in {'loading', 'running'}
             else 'held' if record.get('gameplay_uncertain') else batch.get('phase', 'idle'))
    live = (browser.get('report') or {}).get('batch') or {}
    completed = {row.get('id') for row in live.get('completed', []) if isinstance(row, dict)} if live.get('mode') == 'daily' else set()
    slots = []
    failures = record.get('reward_one_runs') or {}
    for identity, title in DAILY.items():
        row = rows.get(identity)
        status = ('claimed' if identity in completed or row and row.get('claimed') is True
                  else 'locked' if row and row.get('hasKey') is False
                  else 'exhausted' if row and row.get('exhausted') is True
                  else 'pending' if row else 'unknown')
        failure = failures.get(identity) if isinstance(failures, dict) else None
        lost_today = (isinstance(failure, dict) and failure.get('daily_id') == identity
                      and failure.get('outcome') == 'defeat'
                      and isinstance(failure.get('final'), dict)
                      and failure['final'].get('day') == _day(time.time()))
        slots.append({'id': identity, 'title': title, 'state': status, 'defeated_today': lost_today})
    for arena, title in ARENAS.items():
        status = (observation.get('trials') or {}).get(arena) or {}
        used = status.get('used')
        live_remaining = (live.get('quotas') or {}).get(arena) if live.get('mode') == 'trials' else None
        if type(live_remaining) is int and 0 <= live_remaining <= 3:
            used = 3 - live_remaining
        known = type(used) is int and 0 <= used <= 3
        for index in range(3):
            slots.append({'id': f'{arena}:{index + 1}', 'title': f'{title}·第{index + 1}次',
                          'state': 'used' if known and index < used else 'pending' if known else 'unknown'})
    return {'day': observation.get('day'), 'phase': phase, 'slots': slots,
            'claimed': sum(row['state'] == 'claimed' for row in slots[:7]),
            'trial_used': sum(row['state'] == 'used' for row in slots[7:]),
            'next_at': record.get('rewards_next_at'), 'blocked_reason': record.get('rewards_block_reason', ''),
            'single_daily_id': browser.get('daily_id') if valid_daily_id(browser.get('daily_id')) else None,
            'victories_this_batch': batch.get('trial_victories', 0)}


async def _blocked(identity_id, record, reason, now):
    from . import qingxi_quests as quests
    record['rewards_block_reason'] = reason
    notice = record.get('rewards_block_notice') or {}
    if notice.get('day') == _day(now) and notice.get('reason') == reason:
        return
    notice = {'day': _day(now), 'reason': reason, 'state': 'attempted', 'at': now}
    record['rewards_block_notice'] = notice
    quests.save()
    try:
        sent = await send_audit_log('青溪每日副本/试炼暂缓｜' + reason,
                                   scope='identity', send_as_id=identity_id, priority='normal', limit=700)
        notice['state'] = 'sent' if sent else 'unconfirmed'
    except Exception:
        notice['state'] = 'unconfirmed'
    finally:
        quests.save()


async def scheduler(now):
    from . import qingxi_quests as quests
    from . import cave_treasure_runtime as cave
    config = copy.deepcopy(get_expedition_config().get('qingxi_rewards', {}))
    if config.get('enabled') is not True or not get_global_enabled() or not _entry_url() or quests._TASKS:
        return
    for identity_id in config.get('participant_identity_ids', []):
        record = quests.records().setdefault(str(identity_id), {})
        if record.get('phase') in {'running', 'loading'}:
            continue
        due = record.get('rewards_next_at', 0)
        if type(due) not in (int, float) or not math.isfinite(due) or due > now:
            continue
        if not is_cave_public_identity_available(identity_id):
            continue
        batch = record.get('reward_batch') or {}
        stopped = batch.get('stop_for_day') and (batch.get('before') or {}).get('day') == _day(now)
        defeat = defeated_today(identity_id, now)
        if defeat or stopped:
            record['rewards_next_at'] = _next_day(now, identity_id)
            reason = ('每日副本或试炼已确认战败' if defeat else '今日试炼结算需复核') + '，自动批次暂缓，保留剩余奖励次数待手动验证'
            await _blocked(identity_id, record, reason, now)
            quests.save()
            continue
        reason = get_activity_block_reason(identity_id)
        if reason or record.get('phase') == 'held' or record.get('pending') or record.get('gameplay_uncertain'):
            record['rewards_next_at'] = now + 1800
            await _blocked(identity_id, record, reason or '青溪原操作仍待核对', now)
            quests.save()
            if ((record.get('browser') or {}).get('workflow') == 'rewards'
                    and not get_activity_block_reason(identity_id, include_qingxi=False)
                    and not cave._public_entry_lock(identity_id).locked()):
                await quests.action({'action': 'rewards_refresh', 'send_as_id': identity_id})
                return
            continue
        if cave._public_entry_lock(identity_id).locked():
            continue
        # Reserve before enqueueing, including a refused enqueue or lost return.
        record['rewards_next_at'] = now + 1800
        record['rewards_block_reason'] = ''
        quests.save()
        await quests.action({'action': 'rewards', 'send_as_id': identity_id})
        return


def snapshots():
    from . import qingxi_quests as quests
    from . import changfeng_trial as trial
    selected = {str(identity) for identity in get_expedition_config().get('qingxi_rewards', {}).get('participant_identity_ids', [])}
    records = quests.records()
    result = {}
    for key in sorted(set(records) | selected):
        record = records.get(key, {})
        if not isinstance(record, dict) or not (key in selected or record.get('reward_batch')
                or record.get('reward_inspection') or (record.get('browser') or {}).get('workflow') == 'rewards'):
            continue
        item = progress(record)
        if (trial._records().get(key) or {}).get('auto_pause'):
            item.update(auto_paused=True, blocked_reason='试炼已确认战败，自动试炼暂停，需手动单场验证通过')
        elif defeated_daily_today(record, _day(time.time())):
            item.update(auto_paused=True, blocked_reason='每日副本今日已确认战败，整批暂停；其余未败单项仍可分别验收')
        result[key] = item
    return result
