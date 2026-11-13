"""Upstream spirit-beast decisions with local persistence and guarded transport.

Decision rules ported from wxjerry/xiuxian 2190004. Sessions remain ephemeral.
"""
import asyncio
import random
import re
import time
from collections.abc import MutableMapping
from contextvars import ContextVar
from datetime import datetime, timedelta

from ..config import RETRY_MAX_SEC, TZ_LOCAL
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log
from ..timing import fmt_abs_ts, fmt_remaining, parse_wait_time

_CALL = ContextVar("spirit_beast_call")
_RECORD = ContextVar("spirit_beast_record")
NEXT_DAY_COMMAND_WINDOW_START_HOUR_LOCAL = 10
NEXT_DAY_COMMAND_WINDOW_END_HOUR_LOCAL = 15


class _RecordProxy(MutableMapping):
    def __getitem__(self, key):
        return _RECORD.get()[key]

    def __setitem__(self, key, value):
        _RECORD.get()[key] = value

    def __delitem__(self, key):
        del _RECORD.get()[key]

    def __iter__(self):
        return iter(_RECORD.get())

    def __len__(self):
        return len(_RECORD.get())


state = _RecordProxy()


SPIRIT_BEAST_API_START = "/api/miniapp/xianxia-spirit-beast/start"
SPIRIT_BEAST_API_ACTION = "/api/miniapp/xianxia-spirit-beast/action"
SPIRIT_BEAST_API_EXPEDITION_START = "/api/miniapp/xianxia-spirit-beast/expedition/start"
SPIRIT_BEAST_API_EXPEDITION_CHOOSE = "/api/miniapp/xianxia-spirit-beast/expedition/choose"
SPIRIT_BEAST_API_ABYSS_ENTER = "/api/miniapp/xianxia-spirit-beast/abyss/enter"
SPIRIT_BEAST_HTTP_TIMEOUT = (5, 20)
SPIRIT_BEAST_RETRY_MIN_SEC = max(60, RETRY_MAX_SEC + 5)
SPIRIT_BEAST_RETRY_MAX_SEC = SPIRIT_BEAST_RETRY_MIN_SEC + 20
SPIRIT_BEAST_IDLE_RECHECK_MIN_SEC = 25 * 60
SPIRIT_BEAST_IDLE_RECHECK_MAX_SEC = 45 * 60
SPIRIT_BEAST_MAX_RUNS_PER_PASS = 3
SPIRIT_BEAST_MAX_CHOICES_PER_RUN = 3
SPIRIT_BEAST_STAMINA_MAX = 100
SPIRIT_BEAST_INTERACTION_FALLBACK_COOLDOWN_SEC = 90 * 60
SPIRIT_BEAST_TEMPERAMENT_INTERACTIONS = {
    "温顺": "抚摸",
    "护主": "抚摸",
    "孤傲": "梳毛",
    "高傲": "梳毛",
    "贪玩": "逗弄",
    "顽皮": "逗弄",
    "凶悍": "训练",
    "凶厉": "训练",
    "好斗": "训练",
    "机警": "安抚",
    "胆小": "安抚",
    "敏感": "安抚",
}
SPIRIT_BEAST_INTERACTION_COOLDOWN_ERRORS = {
    "beast_action_failed",
    "interact_cooldown",
    "interact_daily_limit",
    "interaction_cooldown",
    "interaction_daily_limit",
}
SPIRIT_BEAST_INTERACTION_SKIP_ERRORS = {
    "beast_busy",
    "beast_missing",
    "stale_beast",
    "stamina_full",
}
SPIRIT_BEAST_ABYSS_MIN_STAMINA = 30
SPIRIT_BEAST_ABYSS_READY_GRACE_SEC = 2
SPIRIT_BEAST_PATH = "/miniapp/xianxia-spirit-beast"
SPIRIT_BEAST_ALLOWED_PATHS = {SPIRIT_BEAST_PATH, "/xianxia_spirit_beast_miniapp.html"}
SPIRIT_BEAST_ALLOWED_TME_HOSTS = {"t.me", "telegram.me"}
RE_SPIRIT_BEAST_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_])spiritbeast[_-][A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])",
    re.I,
)
RE_SPIRIT_BEAST_SENSITIVE_QUERY = re.compile(
    r"(?P<key>tgWebAppData|initData|init_data|query_id|hash|signature|user|token|sessionToken|session_token)=([^&#\s]+)",
    re.I,
)

ZONE_LABELS = {
    "spirit_eye": "灵眼泉脉",
    "ghost_mire": "阴魂雾泽",
    "frost_cave": "玄冰寒潭",
    "relic": "古修遗址",
}
ZONE_LINEAGE_WEIGHTS = {
    "spirit_eye": {
        "spirit_seeker": 150,
        "water_drake": 105,
        "leopard_qilin": 70,
        "wind_scout": 62,
    },
    "ghost_mire": {
        "soul_warden": 160,
        "spirit_fox": 95,
        "steadfast_guardian": 56,
    },
    "frost_cave": {
        "frost_centipede": 150,
        "water_drake": 105,
        "wind_scout": 68,
        "leopard_qilin": 62,
    },
    "relic": {
        "gold_swarm": 150,
        "jade_spider": 125,
        "spirit_seeker": 105,
        "spirit_vine": 65,
    },
}
APPROACH_KEYWORDS = {
    "trace": (
        "痕", "迹", "辨", "伪", "流", "脉", "气息", "寻找", "观察", "侦", "路径", "暗流", "泉", "缝隙",
    ),
    "gift": (
        "破", "封", "壳", "阵", "器", "傀儡", "阴魂", "鬼", "寒", "追", "毒", "缚", "敌", "矿", "金铁",
    ),
    "bond": (
        "契", "诱", "乱", "迷", "撤", "协", "护", "险", "稳定", "纪律", "死路", "共同", "主人",
    ),
}
LINEAGE_APPROACH_WEIGHTS = {
    "spirit_seeker": {"trace": 34, "gift": 8, "bond": 10},
    "water_drake": {"trace": 28, "gift": 12, "bond": 12},
    "wind_scout": {"trace": 25, "gift": 14, "bond": 10},
    "leopard_qilin": {"trace": 22, "gift": 23, "bond": 8},
    "soul_warden": {"trace": 8, "gift": 36, "bond": 18},
    "frost_centipede": {"trace": 8, "gift": 34, "bond": 8},
    "gold_swarm": {"trace": 10, "gift": 34, "bond": 8},
    "jade_spider": {"trace": 8, "gift": 28, "bond": 24},
    "spirit_fox": {"trace": 25, "gift": 10, "bond": 26},
    "steadfast_guardian": {"trace": 7, "gift": 20, "bond": 30},
    "spirit_vine": {"trace": 8, "gift": 24, "bond": 24},
    "venom_insect": {"trace": 12, "gift": 28, "bond": 8},
}


class SpiritBeastMiniappError(Exception):
    def __init__(self, message, *, status=0, payload=None):
        super().__init__(str(message or "万兽谷请求失败"))
        self.status = int(status or 0)
        self.payload = payload if isinstance(payload, dict) else {}


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default or 0)


def _miniapp_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _mark_spirit_beast_dirty(identity_id=None):
    mark_dirty()


def _save_spirit_beast_state(identity_id=None):
    mark_dirty()
    if save_state() is False:
        raise SpiritBeastMiniappError("spirit_beast_state_save_failed")


def _sanitize_spirit_beast_sensitive_text(value):
    text = RE_SPIRIT_BEAST_SENSITIVE_QUERY.sub(
        lambda match: f"{match.group('key')}=[redacted]",
        str(value or ""),
    )
    return RE_SPIRIT_BEAST_TOKEN.sub("spiritbeast_[redacted]", text)


def sanitize_spirit_beast_error(error):
    return _sanitize_spirit_beast_sensitive_text(error)[:220]


async def _request_spirit_beast(path, token, init_data, **extra):
    return await _CALL.get()(path, token, init_data, **extra)


def _attempt_snapshot(data):
    attempts = data.get("attempts") if isinstance(data, dict) and isinstance(data.get("attempts"), dict) else {}
    used = max(0, _as_int(attempts.get("used")))
    limit = max(0, _as_int(attempts.get("limit")))
    remaining = max(0, _as_int(attempts.get("remaining"), max(0, limit - used)))
    return used, limit, remaining


def _beast_lineage_key(beast):
    lineage = beast.get("lineage") if isinstance(beast, dict) and isinstance(beast.get("lineage"), dict) else {}
    return str(lineage.get("key") or "").strip()


def _eligible_beasts(data):
    beasts = data.get("beasts") if isinstance(data, dict) and isinstance(data.get("beasts"), list) else []
    return [
        beast
        for beast in beasts
        if isinstance(beast, dict)
        and (
            _miniapp_bool(beast.get("canExpedition"))
            or (
                str(beast.get("status") or "").strip() == "休息中"
                and _as_int(beast.get("stamina")) >= 24
            )
        )
    ]


def _beasts(data):
    beasts = data.get("beasts") if isinstance(data, dict) and isinstance(data.get("beasts"), list) else []
    return [beast for beast in beasts if isinstance(beast, dict)]


def _find_beast(data, beast_id):
    beast_id = _as_int(beast_id)
    return next((beast for beast in _beasts(data) if _as_int(beast.get("id")) == beast_id), None)


def _is_active_beast(beast):
    return bool(
        isinstance(beast, dict)
        and (
            _miniapp_bool(beast.get("isActive"))
            or str(beast.get("status") or "").strip() == "出战中"
        )
    )


def _new_interaction_stats():
    return {
        "requests": 0,
        "touched_ids": set(),
        "blocked_ids": set(),
    }


def _choose_beast_interaction(beast):
    status = str((beast or {}).get("status") or "").strip()
    if any(keyword in status for keyword in ("受伤", "养伤", "重伤")):
        return "安抚"
    temperament = str((beast or {}).get("temperament") or "").strip()
    return SPIRIT_BEAST_TEMPERAMENT_INTERACTIONS.get(temperament, "抚摸")


def _interaction_cooldowns():
    raw = state.get("spirit_beast_interaction_cooldowns")
    if not isinstance(raw, dict):
        return {}
    cooldowns = {}
    for beast_id, ready_at in raw.items():
        key = str(_as_int(beast_id))
        try:
            value = float(ready_at or 0)
        except (TypeError, ValueError):
            continue
        if key != "0" and value > 0:
            cooldowns[key] = value
    return cooldowns


def _numeric_miniapp_time(value, now):
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if numeric <= 0:
        return 0.0
    if numeric >= 1_000_000_000_000:
        return numeric / 1000
    if numeric >= 1_000_000_000:
        return numeric
    return float(now or time.time()) + numeric


def _interaction_ready_at(data, beast_id, now):
    current = float(now or time.time())
    beast = _find_beast(data, beast_id)
    containers = [beast]
    if isinstance(data, dict):
        for key in ("interaction", "interactionResult", "actionResult", "cooldown"):
            value = data.get(key)
            if isinstance(value, dict):
                containers.append(value)
        containers.append(data)

    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in (
            "interactionReadyAt",
            "interactionCooldownUntil",
            "nextInteractionAt",
            "cooldownUntil",
            "retryAt",
            "readyAt",
        ):
            value = container.get(key)
            ready_at = _parse_miniapp_time(value) or _numeric_miniapp_time(value, current)
            if ready_at > current:
                return ready_at
        for key in (
            "interactionCooldownSeconds",
            "interactionRemainingSeconds",
            "cooldownRemainingSeconds",
            "retryAfterSeconds",
            "retryAfter",
            "remainingSeconds",
        ):
            ready_at = _numeric_miniapp_time(container.get(key), current)
            if ready_at > current:
                return ready_at

    message = str(data.get("message") or "") if isinstance(data, dict) else ""
    if "再互动" in message:
        remaining_seconds = parse_wait_time(message)
        if remaining_seconds > 0:
            return current + remaining_seconds + 1
    return current + SPIRIT_BEAST_INTERACTION_FALLBACK_COOLDOWN_SEC


def _sync_interaction_schedule(data, now, cooldowns=None):
    current = float(now or time.time())
    cooldowns = dict(cooldowns if isinstance(cooldowns, dict) else _interaction_cooldowns())
    known_ids = {str(_as_int(beast.get("id"))) for beast in _beasts(data)}
    cooldowns = {
        beast_id: float(ready_at)
        for beast_id, ready_at in cooldowns.items()
        if beast_id in known_ids and float(ready_at or 0) > current
    }
    pending_times = []
    for beast in _beasts(data):
        beast_id = str(_as_int(beast.get("id")))
        if beast_id == "0" or _as_int(beast.get("stamina")) >= SPIRIT_BEAST_STAMINA_MAX:
            continue
        ready_at = float(cooldowns.get(beast_id, 0) or 0)
        pending_times.append(ready_at if ready_at > current else current + 1)
    state["spirit_beast_interaction_cooldowns"] = cooldowns
    state["spirit_beast_interaction_next_time"] = min(pending_times) if pending_times else 0
    return state["spirit_beast_interaction_next_time"]


def _merge_interaction_stats(*items):
    merged = _new_interaction_stats()
    for item in items:
        if not isinstance(item, dict):
            continue
        merged["requests"] += max(0, _as_int(item.get("requests")))
        merged["touched_ids"].update(item.get("touched_ids") or ())
        merged["blocked_ids"].update(item.get("blocked_ids") or ())
    return merged


def _format_interaction_summary(data, stats):
    if not isinstance(stats, dict) or _as_int(stats.get("requests")) <= 0:
        return ""
    beasts = _beasts(data)
    full = sum(_as_int(beast.get("stamina")) >= SPIRIT_BEAST_STAMINA_MAX for beast in beasts)
    return f"灵契互动：{len(stats.get('touched_ids') or ())}只已恢复·体力已满{full}/{len(beasts)}"


async def _restore_all_beast_stamina(token, init_data, data, now):
    current = data
    stats = _new_interaction_stats()
    current_time = float(now or time.time())
    cooldowns = _interaction_cooldowns()

    candidates = sorted(
        (
            beast
            for beast in _beasts(current)
            if _as_int(beast.get("id")) > 0
            and _as_int(beast.get("stamina")) < SPIRIT_BEAST_STAMINA_MAX
        ),
        key=lambda beast: (
            str(beast.get("status") or "").strip() not in {"休息中", "出战中"},
            _as_int(beast.get("stamina")),
            _as_int(beast.get("id")),
        ),
    )
    for candidate in candidates:
        beast_id = _as_int(candidate.get("id"))
        if beast_id <= 0:
            continue
        beast = _find_beast(current, beast_id)
        if beast is None or _as_int(beast.get("stamina")) >= SPIRIT_BEAST_STAMINA_MAX:
            continue
        if float(cooldowns.get(str(beast_id), 0) or 0) > current_time:
            continue

        previous_stamina = max(0, _as_int(beast.get("stamina")))
        stats["requests"] += 1
        try:
            response = await _request_spirit_beast(
                SPIRIT_BEAST_API_ACTION,
                token,
                init_data,
                action="interact",
                beastId=beast_id,
                interaction=_choose_beast_interaction(beast),
            )
        except SpiritBeastMiniappError as exc:
            code = str(exc or "").strip().lower()
            if _beasts(exc.payload):
                current = exc.payload
            if code in SPIRIT_BEAST_INTERACTION_COOLDOWN_ERRORS or code in SPIRIT_BEAST_INTERACTION_SKIP_ERRORS:
                stats["blocked_ids"].add(beast_id)
                cooldowns[str(beast_id)] = _interaction_ready_at(exc.payload, beast_id, current_time)
                continue
            raise

        current = response
        updated = _find_beast(current, beast_id)
        if updated is None:
            raise SpiritBeastMiniappError("灵契互动响应缺少目标灵兽", payload=current)
        current_stamina = max(0, _as_int(updated.get("stamina")))
        cooldowns[str(beast_id)] = _interaction_ready_at(current, beast_id, current_time)
        if current_stamina > previous_stamina:
            stats["touched_ids"].add(beast_id)
        else:
            stats["blocked_ids"].add(beast_id)
    _sync_interaction_schedule(current, current_time, cooldowns)
    return current, stats


def _abyss_data(data):
    return data.get("abyss") if isinstance(data, dict) and isinstance(data.get("abyss"), dict) else None


def _parse_miniapp_time(value):
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _sync_abyss_state(data, now):
    abyss = _abyss_data(data)
    if abyss is None:
        state["spirit_beast_abyss_supported"] = False
        state["spirit_beast_abyss_phase"] = "unsupported"
        state["spirit_beast_abyss_next_time"] = 0
        return False

    current = float(now or time.time())
    remaining = max(0, _as_int(abyss.get("remainingSeconds")))
    ready_at = _parse_miniapp_time(abyss.get("readyAt"))
    ready = _miniapp_bool(abyss.get("ready")) and remaining <= 0 and (ready_at <= 0 or ready_at <= current)
    state["spirit_beast_abyss_supported"] = True
    if ready:
        state["spirit_beast_abyss_phase"] = "ready"
        state["spirit_beast_abyss_next_time"] = current
    else:
        if ready_at > 0:
            next_time = ready_at + SPIRIT_BEAST_ABYSS_READY_GRACE_SEC
        elif remaining > 0:
            next_time = current + remaining + SPIRIT_BEAST_ABYSS_READY_GRACE_SEC
        else:
            next_time = current + random.uniform(
                SPIRIT_BEAST_IDLE_RECHECK_MIN_SEC,
                SPIRIT_BEAST_IDLE_RECHECK_MAX_SEC,
            )
        state["spirit_beast_abyss_phase"] = "cooldown"
        state["spirit_beast_abyss_next_time"] = max(current + 1, next_time)
    return ready


def _abyss_min_stamina(data):
    abyss = _abyss_data(data) or {}
    return max(SPIRIT_BEAST_ABYSS_MIN_STAMINA, _as_int(abyss.get("minStamina")))


def _choose_abyss_beast(data):
    minimum_stamina = _abyss_min_stamina(data)
    candidates = [
        beast
        for beast in _beasts(data)
        if _as_int(beast.get("stamina")) >= minimum_stamina
        and (
            _is_active_beast(beast)
            or _miniapp_bool(beast.get("canExploreAbyss"))
            or str(beast.get("status") or "").strip() == "休息中"
        )
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda beast: (
            _as_int(beast.get("combatPower")),
            _as_int(beast.get("stamina")),
            _as_int(beast.get("bond")),
            -_as_int(beast.get("id")),
        ),
    )


def _zone_key(zone):
    return str((zone or {}).get("key") or "").strip()


def _zone_label(zone):
    key = _zone_key(zone)
    return str((zone or {}).get("title") or ZONE_LABELS.get(key) or key or "未知区域").strip()


def _beast_zone_score(beast, zone):
    zone_key = _zone_key(zone)
    lineage_key = _beast_lineage_key(beast)
    lineage = beast.get("lineage") if isinstance(beast.get("lineage"), dict) else {}
    favored_zones = {str(item or "").strip() for item in (lineage.get("favoredZones") or [])}
    score = ZONE_LINEAGE_WEIGHTS.get(zone_key, {}).get(lineage_key, 0)
    if zone_key in favored_zones:
        score += 140
    score += min(40, max(0, _as_int(beast.get("combatPower"))) / 30)
    score += max(0, _as_int(beast.get("stamina"))) * 0.16
    score += max(0, _as_int(beast.get("bond"))) * 0.018
    score += max(0, _as_int(beast.get("loyalty"))) * 0.08
    score += max(0, _as_int(beast.get("mood"))) * 0.05
    return score


def _choose_beast_and_zone(data):
    beasts = _eligible_beasts(data)
    zones = data.get("zones") if isinstance(data, dict) and isinstance(data.get("zones"), list) else []
    zones = [zone for zone in zones if isinstance(zone, dict) and _zone_key(zone)]
    if not beasts or not zones:
        return None, None
    return max(
        ((beast, zone) for beast in beasts for zone in zones),
        key=lambda pair: (
            _beast_zone_score(pair[0], pair[1]),
            _as_int(pair[0].get("stamina")),
            _as_int(pair[0].get("combatPower")),
            -_as_int(pair[0].get("id")),
        ),
    )


def _choice_stats():
    raw = state.get("spirit_beast_choice_stats")
    return dict(raw) if isinstance(raw, dict) else {}


def _choice_stat_key(run, encounter):
    zone = run.get("zone") if isinstance(run, dict) and isinstance(run.get("zone"), dict) else {}
    beast = run.get("beast") if isinstance(run, dict) and isinstance(run.get("beast"), dict) else {}
    lineage_key = str(beast.get("lineageKey") or beast.get("lineage") or "").strip()
    title = str((encounter or {}).get("title") or "").strip()
    return "|".join((_zone_key(zone), lineage_key, title))[:180]


def _enrich_expedition_beast(data):
    run = data.get("expedition") if isinstance(data, dict) and isinstance(data.get("expedition"), dict) else None
    if not run:
        return None
    run_beast = run.get("beast") if isinstance(run.get("beast"), dict) else {}
    beast_id = _as_int(run_beast.get("id"))
    beasts = data.get("beasts") if isinstance(data.get("beasts"), list) else []
    profile = next(
        (item for item in beasts if isinstance(item, dict) and _as_int(item.get("id")) == beast_id),
        None,
    )
    if profile is not None:
        run_beast.setdefault("lineageKey", _beast_lineage_key(profile))
        run["beast"] = run_beast
    return run


def _choose_approach(run):
    encounter = run.get("encounter") if isinstance(run, dict) and isinstance(run.get("encounter"), dict) else {}
    approaches = encounter.get("approaches") if isinstance(encounter.get("approaches"), list) else []
    keys = [str(item.get("key") or "").strip() for item in approaches if isinstance(item, dict)]
    keys = [key for key in keys if key]
    if not keys:
        raise SpiritBeastMiniappError("当前行迹缺少可选应对方式")

    beast = run.get("beast") if isinstance(run.get("beast"), dict) else {}
    lineage_key = str(beast.get("lineageKey") or beast.get("lineage") or "").strip()
    context = " ".join(
        str(value or "")
        for value in (
            encounter.get("title"),
            encounter.get("text"),
            *(item.get("description") for item in approaches if isinstance(item, dict)),
        )
    )
    scores = {key: 10.0 for key in keys}
    for approach, weights in LINEAGE_APPROACH_WEIGHTS.get(lineage_key, {}).items():
        if approach in scores:
            scores[approach] += weights
    for approach, keywords in APPROACH_KEYWORDS.items():
        if approach in scores:
            scores[approach] += sum(9 for keyword in keywords if keyword in context)

    learned = _choice_stats().get(_choice_stat_key(run, encounter))
    if isinstance(learned, dict):
        perfect_candidates = []
        for approach in keys:
            stat = learned.get(approach)
            if not isinstance(stat, dict):
                continue
            count = max(0, _as_int(stat.get("count")))
            total = float(stat.get("total") or 0)
            perfect_count = max(0, _as_int(stat.get("perfect_count")))
            if count > 0 and perfect_count > 0:
                perfect_candidates.append((
                    perfect_count / count,
                    total / count,
                    scores.get(approach, 0),
                    -keys.index(approach),
                    approach,
                ))
        if perfect_candidates:
            return max(perfect_candidates)[-1]

        untried = [
            approach
            for approach in keys
            if not isinstance(learned.get(approach), dict)
            or max(0, _as_int(learned[approach].get("count"))) <= 0
        ]
        if untried:
            return max(untried, key=lambda key: (scores.get(key, 0), -keys.index(key)))

        learned_candidates = []
        for approach in keys:
            stat = learned.get(approach)
            if not isinstance(stat, dict):
                continue
            count = max(0, _as_int(stat.get("count")))
            total = float(stat.get("total") or 0)
            if count > 0:
                learned_candidates.append((
                    total / count,
                    min(count, 3),
                    scores.get(approach, 0),
                    -keys.index(approach),
                    approach,
                ))
        if learned_candidates:
            return max(learned_candidates)[-1]
    return max(keys, key=lambda key: (scores.get(key, 0), -keys.index(key)))


def _record_choice_result(run, encounter, approach, outcome):
    if not isinstance(outcome, dict):
        return
    score = max(0, _as_int(outcome.get("score")))
    if score <= 0:
        return
    stats = _choice_stats()
    key = _choice_stat_key(run, encounter)
    entries = stats.get(key) if isinstance(stats.get(key), dict) else {}
    current = entries.get(approach) if isinstance(entries.get(approach), dict) else {}
    previous_count = max(0, _as_int(current.get("count")))
    previous_total = max(0, _as_int(current.get("total")))
    previous_perfect_count = max(0, _as_int(current.get("perfect_count")))
    is_perfect = str(outcome.get("grade") or "").strip().lower() == "perfect"
    if previous_count < 50:
        next_count = previous_count + 1
        next_total = previous_total + score
        next_perfect_count = previous_perfect_count + int(is_perfect)
    else:
        next_count = 50
        next_total = round((previous_total / previous_count) * 49 + score)
        next_perfect_count = round((previous_perfect_count / previous_count) * 49 + int(is_perfect))
    entries[approach] = {
        "count": next_count,
        "total": min(5000, next_total),
        "perfect_count": min(next_count, next_perfect_count),
    }
    stats[key] = entries
    if len(stats) > 160:
        stats = dict(list(stats.items())[-160:])
    state["spirit_beast_choice_stats"] = stats


def _sync_spirit_beast_state(data, now):
    beasts = _beasts(data)
    used, limit, remaining = _attempt_snapshot(data)
    expedition = data.get("expedition") if isinstance(data, dict) and isinstance(data.get("expedition"), dict) else None
    state["spirit_beast_last_synced_at"] = float(now or time.time())
    state["spirit_beast_daily_used"] = used
    state["spirit_beast_daily_limit"] = limit
    state["spirit_beast_daily_remaining"] = remaining
    state["spirit_beast_count"] = len(beasts)
    state["spirit_beast_eligible_count"] = len(_eligible_beasts(data))
    state["spirit_beast_phase"] = "running" if expedition else "idle"
    _sync_abyss_state(data, now)
    return used, limit, remaining


def _format_reward(reward):
    if not isinstance(reward, dict):
        return ""
    item_name = str(reward.get("itemName") or reward.get("name") or "").strip()
    quantity = max(0, _as_int(reward.get("quantity") or reward.get("count")))
    grade = str(reward.get("grade") or "").strip()
    if item_name:
        return f"{item_name}x{quantity or 1}{f'（{grade}）' if grade else ''}"
    return grade


def _schedule_spirit_beast_retry(now, minimum=None, maximum=None):
    minimum = float(minimum if minimum is not None else SPIRIT_BEAST_RETRY_MIN_SEC)
    maximum = float(maximum if maximum is not None else SPIRIT_BEAST_RETRY_MAX_SEC)
    state["next_spirit_beast_time"] = float(now or time.time()) + random.uniform(minimum, maximum)
    return state["next_spirit_beast_time"]


def _schedule_spirit_beast_expedition_retry(now, minimum=None, maximum=None):
    minimum = float(minimum if minimum is not None else SPIRIT_BEAST_IDLE_RECHECK_MIN_SEC)
    maximum = float(maximum if maximum is not None else SPIRIT_BEAST_IDLE_RECHECK_MAX_SEC)
    state["spirit_beast_expedition_next_time"] = float(now or time.time()) + random.uniform(minimum, maximum)
    return state["spirit_beast_expedition_next_time"]


def _schedule_spirit_beast_next_day(now):
    current = datetime.fromtimestamp(float(now or time.time()), TZ_LOCAL)
    next_day = current + timedelta(days=1)
    start = next_day.replace(
        hour=NEXT_DAY_COMMAND_WINDOW_START_HOUR_LOCAL,
        minute=0,
        second=0,
        microsecond=0,
    )
    end = next_day.replace(
        hour=NEXT_DAY_COMMAND_WINDOW_END_HOUR_LOCAL,
        minute=0,
        second=0,
        microsecond=0,
    )
    state["spirit_beast_expedition_next_time"] = random.uniform(start.timestamp(), max(start.timestamp(), end.timestamp() - 1))
    return state["spirit_beast_expedition_next_time"]


def _refresh_next_spirit_beast_time(now):
    current = float(now or time.time())
    due_times = []
    expedition_next = float(state.get("spirit_beast_expedition_next_time", 0) or 0)
    if expedition_next > 0:
        due_times.append(expedition_next)
    if state.get("spirit_beast_abyss_supported"):
        abyss_next = float(state.get("spirit_beast_abyss_next_time", 0) or 0)
        if abyss_next > 0:
            due_times.append(abyss_next)
    interaction_next = float(state.get("spirit_beast_interaction_next_time", 0) or 0)
    if interaction_next > 0:
        due_times.append(interaction_next)
    if _as_int(state.get("spirit_beast_restore_active_beast_id")) > 0:
        restore_after = float(state.get("spirit_beast_restore_active_after", 0) or 0)
        due_times.append(restore_after if restore_after > 0 else current + 1)
    if not due_times:
        due_times.append(current + random.uniform(
            SPIRIT_BEAST_IDLE_RECHECK_MIN_SEC,
            SPIRIT_BEAST_IDLE_RECHECK_MAX_SEC,
        ))
    state["next_spirit_beast_time"] = max(current + 1, min(due_times))
    return state["next_spirit_beast_time"]


def clear_spirit_beast_state(*, persist=False, keep_last_error=False):
    last_error = state.get("spirit_beast_last_error") if keep_last_error else ""
    stats = _choice_stats()
    state["next_spirit_beast_time"] = 0
    state["spirit_beast_expedition_next_time"] = 0
    state["spirit_beast_abyss_next_time"] = 0
    state["spirit_beast_interaction_next_time"] = 0
    state["spirit_beast_restore_active_after"] = 0
    state["spirit_beast_last_synced_at"] = 0
    state["spirit_beast_phase"] = "idle"
    state["spirit_beast_daily_used"] = 0
    state["spirit_beast_daily_limit"] = 0
    state["spirit_beast_daily_remaining"] = 0
    state["spirit_beast_count"] = 0
    state["spirit_beast_eligible_count"] = 0
    state["spirit_beast_last_beast"] = ""
    state["spirit_beast_last_zone"] = ""
    state["spirit_beast_last_score"] = 0
    state["spirit_beast_last_reward"] = ""
    state["spirit_beast_last_result"] = ""
    state["spirit_beast_last_error"] = last_error or ""
    state["spirit_beast_choice_stats"] = stats
    state["spirit_beast_interaction_cooldowns"] = {}
    state["spirit_beast_abyss_supported"] = False
    state["spirit_beast_abyss_phase"] = "unknown"
    state["spirit_beast_abyss_last_beast"] = ""
    state["spirit_beast_abyss_last_enemy"] = ""
    state["spirit_beast_abyss_last_reward"] = ""
    state["spirit_beast_abyss_last_result"] = ""
    state["spirit_beast_restore_active_beast_id"] = 0
    state["spirit_beast_restore_active_beast_name"] = ""
    if persist:
        _save_spirit_beast_state()
    else:
        _mark_spirit_beast_dirty()


def get_spirit_beast_status_text():
    used = max(0, _as_int(state.get("spirit_beast_daily_used")))
    limit = max(0, _as_int(state.get("spirit_beast_daily_limit")))
    daily_text = f"{used}/{limit}" if limit > 0 else "未同步"
    lines = [
        "🐾 驭灵行迹",
        f"- 已启用：{'是' if state.get('spirit_beast_enabled') else '否'}",
        "- 宗门限制：仅万灵宗",
        "- 入口：洞府外府 · 万灵宗·万兽谷 MiniApp",
        f"- 当前阶段：{state.get('spirit_beast_phase') or 'idle'}",
        f"- 今日次数：{daily_text}",
        f"- 可出行灵兽：{max(0, _as_int(state.get('spirit_beast_eligible_count')))}/{max(0, _as_int(state.get('spirit_beast_count')))}",
        f"- 下次执行：{fmt_abs_ts(state.get('next_spirit_beast_time', 0) or 0)}（{fmt_remaining(state.get('next_spirit_beast_time', 0) or 0)}）",
    ]
    expedition_next = float(state.get("spirit_beast_expedition_next_time", 0) or 0)
    if expedition_next > 0:
        lines.append(f"- 下次行迹：{fmt_abs_ts(expedition_next)}（{fmt_remaining(expedition_next)}）")
    if state.get("spirit_beast_abyss_supported"):
        abyss_next = float(state.get("spirit_beast_abyss_next_time", 0) or 0)
        abyss_phase = str(state.get("spirit_beast_abyss_phase") or "unknown")
        if abyss_phase == "ready":
            lines.append("- 探渊：可执行")
        elif abyss_next > 0:
            lines.append(f"- 下次探渊：{fmt_abs_ts(abyss_next)}（{fmt_remaining(abyss_next)}）")
    interaction_next = float(state.get("spirit_beast_interaction_next_time", 0) or 0)
    if interaction_next > 0:
        lines.append(f"- 下次灵契互动：{fmt_abs_ts(interaction_next)}（{fmt_remaining(interaction_next)}）")
    last_beast = str(state.get("spirit_beast_last_beast") or "").strip()
    last_zone = str(state.get("spirit_beast_last_zone") or "").strip()
    if last_beast or last_zone:
        lines.append(f"- 最近行迹：{last_beast or '-'} · {last_zone or '-'} · {max(0, _as_int(state.get('spirit_beast_last_score')))}分")
    reward = str(state.get("spirit_beast_last_reward") or "").strip()
    if reward:
        lines.append(f"- 最近奖励：{reward}")
    lines.append(f"- 最近结果：{state.get('spirit_beast_last_result') or '无'}")
    abyss_result = str(state.get("spirit_beast_abyss_last_result") or "").strip()
    if abyss_result:
        lines.append(f"- 最近探渊：{abyss_result}")
    restore_name = str(state.get("spirit_beast_restore_active_beast_name") or "").strip()
    if _as_int(state.get("spirit_beast_restore_active_beast_id")) > 0:
        restore_after = float(state.get("spirit_beast_restore_active_after", 0) or 0)
        lines.append(
            f"- 恢复出战：{restore_name or '原出战灵兽'}"
            + (f"→{fmt_abs_ts(restore_after)}" if restore_after > 0 else "→待重试")
        )
    error = str(state.get("spirit_beast_last_error") or "").strip()
    if error:
        lines.append(f"- 最近异常：{error}")
    return "\n".join(lines)


async def _finish_active_expedition(token, init_data, data):
    current = data
    run = _enrich_expedition_beast(current)
    last_outcome = {}
    for _ in range(SPIRIT_BEAST_MAX_CHOICES_PER_RUN):
        if not run:
            break
        encounter = run.get("encounter") if isinstance(run.get("encounter"), dict) else {}
        approach = _choose_approach(run)
        response = await _request_spirit_beast(
            SPIRIT_BEAST_API_EXPEDITION_CHOOSE,
            token,
            init_data,
            runToken=str(run.get("runToken") or ""),
            seq=max(0, _as_int(run.get("seq"))),
            approach=approach,
        )
        outcome = response.get("outcome") if isinstance(response.get("outcome"), dict) else {}
        _record_choice_result(run, encounter, approach, outcome)
        last_outcome = outcome or last_outcome
        current = response
        run = _enrich_expedition_beast(current)
    if run:
        raise SpiritBeastMiniappError("行迹三段应对后仍未结束")
    return current, last_outcome


def _clear_pending_active_restore():
    state["spirit_beast_restore_active_beast_id"] = 0
    state["spirit_beast_restore_active_beast_name"] = ""
    state["spirit_beast_restore_active_after"] = 0


def _set_pending_active_restore(beast, restore_after=0):
    state["spirit_beast_restore_active_beast_id"] = _as_int((beast or {}).get("id"))
    state["spirit_beast_restore_active_beast_name"] = str((beast or {}).get("name") or "原出战灵兽").strip()
    state["spirit_beast_restore_active_after"] = max(0, float(restore_after or 0))


async def _restore_active_beast_if_due(token, init_data, data, now):
    beast_id = _as_int(state.get("spirit_beast_restore_active_beast_id"))
    if beast_id <= 0:
        return data, ""

    current = data
    beast = _find_beast(current, beast_id)
    if beast is None:
        name = str(state.get("spirit_beast_restore_active_beast_name") or "原出战灵兽")
        _clear_pending_active_restore()
        _save_spirit_beast_state()
        return current, f"恢复出战取消：{name}已不在灵兽袋"
    if _is_active_beast(beast):
        name = str(beast.get("name") or state.get("spirit_beast_restore_active_beast_name") or "原出战灵兽")
        _clear_pending_active_restore()
        _save_spirit_beast_state()
        return current, f"{name}已恢复出战"

    restore_after = float(state.get("spirit_beast_restore_active_after", 0) or 0)
    if restore_after > float(now or time.time()):
        state["spirit_beast_phase"] = "restore_wait"
        return current, ""

    try:
        current = await _request_spirit_beast(
            SPIRIT_BEAST_API_ACTION,
            token,
            init_data,
            action="active",
            beastId=beast_id,
        )
    except Exception:
        state["spirit_beast_restore_active_after"] = float(now or time.time()) + SPIRIT_BEAST_RETRY_MIN_SEC
        _save_spirit_beast_state()
        raise

    restored = _find_beast(current, beast_id)
    player = current.get("player") if isinstance(current.get("player"), dict) else {}
    if restored is not None and not _is_active_beast(restored) and _as_int(player.get("activeBeastId")) != beast_id:
        state["spirit_beast_restore_active_after"] = float(now or time.time()) + SPIRIT_BEAST_RETRY_MIN_SEC
        _save_spirit_beast_state()
        raise SpiritBeastMiniappError("原出战灵兽恢复后仍未进入出战状态", payload=current)
    name = str((restored or beast).get("name") or state.get("spirit_beast_restore_active_beast_name") or "原出战灵兽")
    _clear_pending_active_restore()
    _save_spirit_beast_state()
    return current, f"{name}已恢复出战"


def _format_abyss_rewards(result):
    rewards = result.get("rewards") if isinstance(result, dict) and isinstance(result.get("rewards"), list) else []
    parts = []
    for reward in rewards:
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or reward.get("itemName") or reward.get("itemId") or "").strip()
        quantity = max(0, _as_int(reward.get("quantity")))
        if name:
            parts.append(f"{name}x{quantity or 1}")
    return "、".join(parts)


async def _run_spirit_beast_abyss(token, init_data, data, now):
    current = data
    beast = _choose_abyss_beast(current)
    if beast is None:
        state["spirit_beast_abyss_phase"] = "waiting_beast"
        state["spirit_beast_abyss_next_time"] = float(now or time.time()) + random.uniform(
            SPIRIT_BEAST_IDLE_RECHECK_MIN_SEC,
            SPIRIT_BEAST_IDLE_RECHECK_MAX_SEC,
        )
        return current, "探渊待命：暂无休息中或出战中且体力足够的灵兽"

    beast_id = _as_int(beast.get("id"))
    beast_name = str(beast.get("name") or "未知灵兽").strip()
    was_active = _is_active_beast(beast)
    if was_active:
        _set_pending_active_restore(beast)
        # 先持久化恢复意图，确保召回后即使进程重启也能重新出战。
        _save_spirit_beast_state()
        current = await _request_spirit_beast(
            SPIRIT_BEAST_API_ACTION,
            token,
            init_data,
            action="rest",
            beastId=beast_id,
        )

    try:
        current = await _request_spirit_beast(
            SPIRIT_BEAST_API_ABYSS_ENTER,
            token,
            init_data,
            beastId=beast_id,
        )
        result = current.get("result") if isinstance(current.get("result"), dict) else {}
        if not result:
            raise SpiritBeastMiniappError("探渊响应缺少 result", payload=current)
    except Exception:
        if was_active:
            try:
                current, _ = await _restore_active_beast_if_due(token, init_data, current, now)
            except Exception:
                pass
        raise

    result_beast = result.get("beast") if isinstance(result.get("beast"), dict) else {}
    enemy = result.get("wildBeast") if isinstance(result.get("wildBeast"), dict) else {}
    beast_name = str(result_beast.get("name") or beast_name).strip()
    enemy_name = str(enemy.get("name") or "渊中妖兽").strip()
    title = str(result.get("title") or ("镇渊得胜" if _miniapp_bool(result.get("won")) else "重伤退渊")).strip()
    reward_text = _format_abyss_rewards(result)
    summary = f"探渊：{beast_name}·{title}·{enemy_name}{f'·{reward_text}' if reward_text else ''}"
    state["spirit_beast_abyss_last_beast"] = beast_name
    state["spirit_beast_abyss_last_enemy"] = enemy_name
    state["spirit_beast_abyss_last_reward"] = reward_text
    state["spirit_beast_abyss_last_result"] = f"{beast_name}·{title}·{enemy_name}{f'·{reward_text}' if reward_text else ''}"
    _sync_spirit_beast_state(current, now)

    if was_active:
        recovery_seconds = max(0, _as_int(result.get("recoverySeconds")))
        status_after = str(result.get("statusAfter") or "").strip()
        if recovery_seconds > 0 or status_after == "受伤":
            if recovery_seconds <= 0:
                recovery_seconds = SPIRIT_BEAST_RETRY_MIN_SEC
            restore_after = float(now or time.time()) + recovery_seconds + SPIRIT_BEAST_ABYSS_READY_GRACE_SEC
            state["spirit_beast_restore_active_after"] = restore_after
            state["spirit_beast_phase"] = "restore_wait"
            _save_spirit_beast_state()
            summary += f"·恢复出战→{fmt_abs_ts(restore_after)}"
        else:
            current, restore_summary = await _restore_active_beast_if_due(token, init_data, current, now)
            _sync_spirit_beast_state(current, now)
            if restore_summary:
                summary += f"·{restore_summary}"
    return current, summary


async def _run_spirit_beast_flow(token, init_data, data, now):
    current = data
    _sync_spirit_beast_state(current, now)
    result_parts = []
    interaction_stats = _new_interaction_stats()

    current, restore_summary = await _restore_active_beast_if_due(token, init_data, current, now)
    if restore_summary:
        result_parts.append(restore_summary)
        _sync_spirit_beast_state(current, now)

    current, pre_interaction_stats = await _restore_all_beast_stamina(token, init_data, current, now)
    interaction_stats = _merge_interaction_stats(interaction_stats, pre_interaction_stats)
    _sync_spirit_beast_state(current, now)

    restore_pending = _as_int(state.get("spirit_beast_restore_active_beast_id")) > 0
    abyss_due = (
        state.get("spirit_beast_abyss_supported")
        and not restore_pending
        and float(state.get("spirit_beast_abyss_next_time", 0) or 0) <= float(now or 0)
    )
    if abyss_due:
        current, abyss_summary = await _run_spirit_beast_abyss(token, init_data, current, now)
        if abyss_summary:
            result_parts.append(abyss_summary)

    summaries = []
    final_reason = ""
    active_run = _enrich_expedition_beast(current)
    expedition_due = bool(active_run) or float(state.get("spirit_beast_expedition_next_time", 0) or 0) <= float(now or 0)
    if expedition_due:
        for _ in range(SPIRIT_BEAST_MAX_RUNS_PER_PASS):
            _used, _limit, remaining = _sync_spirit_beast_state(current, now)
            active_run = _enrich_expedition_beast(current)
            selected_beast = None
            selected_zone = None
            if not active_run:
                if remaining <= 0:
                    final_reason = "今日次数已用尽"
                    break
                selected_beast, selected_zone = _choose_beast_and_zone(current)
                if selected_beast is None or selected_zone is None:
                    final_reason = "暂无休息中且体力足够的灵兽"
                    break
                current = await _request_spirit_beast(
                    SPIRIT_BEAST_API_EXPEDITION_START,
                    token,
                    init_data,
                    beastId=_as_int(selected_beast.get("id")),
                    zoneKey=_zone_key(selected_zone),
                )
                active_run = _enrich_expedition_beast(current)
                if not active_run:
                    raise SpiritBeastMiniappError("行迹开局缺少 expedition")

            run_beast = active_run.get("beast") if isinstance(active_run.get("beast"), dict) else {}
            run_zone = active_run.get("zone") if isinstance(active_run.get("zone"), dict) else {}
            beast_name = str(run_beast.get("name") or (selected_beast or {}).get("name") or "未知灵兽").strip()
            zone_name = _zone_label(run_zone or selected_zone)
            current, last_outcome = await _finish_active_expedition(token, init_data, current)
            _sync_spirit_beast_state(current, now)
            history = current.get("history") if isinstance(current.get("history"), list) else []
            latest = history[0] if history and isinstance(history[0], dict) else {}
            score = max(0, _as_int(latest.get("score") or last_outcome.get("score")))
            reward = current.get("reward") if isinstance(current.get("reward"), dict) else None
            reward = reward or (latest.get("reward") if isinstance(latest.get("reward"), dict) else None)
            reward_text = _format_reward(reward)
            state["spirit_beast_last_beast"] = beast_name
            state["spirit_beast_last_zone"] = zone_name
            state["spirit_beast_last_score"] = score
            state["spirit_beast_last_reward"] = reward_text
            summaries.append(f"{beast_name}·{zone_name} {reward_text or '无物品收获'}")

        _used, _limit, remaining = _sync_spirit_beast_state(current, now)
        if remaining <= 0 or final_reason.startswith("暂无"):
            state["spirit_beast_phase"] = "done_today" if remaining <= 0 else "waiting_stamina"
            _schedule_spirit_beast_next_day(now)
        else:
            state["spirit_beast_phase"] = "idle"
            _schedule_spirit_beast_expedition_retry(now)

    if summaries:
        result_parts.append("行迹：" + "｜".join(summaries))
    elif expedition_due and final_reason:
        result_parts.append(final_reason)

    current, post_interaction_stats = await _restore_all_beast_stamina(token, init_data, current, now)
    interaction_stats = _merge_interaction_stats(interaction_stats, post_interaction_stats)
    state["spirit_beast_count"] = len(_beasts(current))
    state["spirit_beast_eligible_count"] = len(_eligible_beasts(current))
    interaction_summary = _format_interaction_summary(current, interaction_stats)
    if interaction_summary:
        result_parts.append(interaction_summary)
    if _as_int(state.get("spirit_beast_restore_active_beast_id")) > 0:
        state["spirit_beast_phase"] = "restore_wait"
    _refresh_next_spirit_beast_time(now)
    result_text = "｜".join(result_parts) if result_parts else "状态已同步"
    state["spirit_beast_last_result"] = result_text
    state["spirit_beast_last_error"] = ""
    _save_spirit_beast_state()
    await send_audit_log(
        f"🐾 驭灵行迹完成→{result_text}｜下次→{fmt_abs_ts(state['next_spirit_beast_time'])}",
        tag="MiniApp",
        limit=320,
    )
    return True


