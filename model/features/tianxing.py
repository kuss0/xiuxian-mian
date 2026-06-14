import copy
import math
import re
import time

from ..config import (
    CMD_TIANXING_CHANGE_FATE,
    CMD_TIANXING_CLEAR_CALAMITY,
    CMD_TIANXING_OBSERVE,
    CMD_TIANXING_PANEL,
    CMD_TIANXING_PREDICT,
    CMD_TIANXING_SET_STAR,
)
from ..persistence import save_state
from ..runtime import send_game_command
from ..state import state, use_identity
from ..timing import fmt_abs_ts, fmt_remaining, has_wait_time, parse_wait_time


TIANXING_PREDICTION_SEC = 8 * 3600
TIANXING_CHANGE_FATE_SEC = 24 * 3600
TIANXING_TIME_BUFFER_SEC = 60
TIANXING_OBSERVATION_STALE_SEC = 24 * 3600
TIANXING_AUTO_STATUS_BACKOFF_SEC = 6 * 3600
TIANXING_AUTO_BLOCK_BACKOFF_SEC = 60 * 60
TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC = 30 * 60
TIANXING_STARS = ("紫微", "天府", "太阴", "贪狼")
TIANXING_ROUTES = ("闭关", "炼制", "探索", "斗法")

RE_BRACKET = re.compile(r"【([^】]+)】")
RE_STAR_EFFECT = re.compile(r"命盘【(?P<star>[^】]+)】照命(?P<desc>[^。\n]*)")
RE_SET_STAR = re.compile(r"你将今日命轨定在\s*【(?P<star>[^】]+)】")
RE_PREDICT = re.compile(r"为\s*【(?P<route>[^】]+)】\s*推下一段命数")
RE_CHANGE_FATE = re.compile(r"为\s*【(?P<route>[^】]+)】\s*预留了一次改命回天")
RE_TIANJI_GAIN = re.compile(r"天机值\s*\+(?P<gain>\d+)")
RE_CONTRIB_GAIN = re.compile(r"宗门贡献\s*\+(?P<gain>\d+)")
RE_TIANJI_VALUE = re.compile(r"天机值[:：]\s*(?P<value>\d+)")
RE_CALAMITY = re.compile(r"逆命劫[:：]\s*(?P<value>\d+)")
RE_CALAMITY_GAIN = re.compile(r"逆命劫\s*\+(?P<gain>\d+)")
RE_COUNTS = re.compile(r"命中\s*/\s*落空\s*/\s*改命[:：]\s*(?P<hit>\d+)\s*/\s*(?P<miss>\d+)\s*/\s*(?P<change>\d+)")
RE_BONUS_GAIN = re.compile(r"因【天星宗】灵脉加持，你额外获得了\s*(?P<gain>\d+)\s*点修为")

TIANXING_OBSERVATION_TIME_KEYS = (
    "last_observed_at",
    "current_prediction_until",
    "current_change_until",
    "auto_next_time",
)


def _is_empty_state_value(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_observation_float(value):
    if _is_empty_state_value(value):
        return 0.0, False
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0, True
    if not math.isfinite(parsed):
        return 0.0, True
    return parsed, False


def _dirty_tianxing_time_fields(value=None):
    if not isinstance(value, dict):
        return []
    dirty_fields = []
    for key in TIANXING_OBSERVATION_TIME_KEYS:
        _parsed, dirty = _parse_observation_float(value.get(key, 0))
        if dirty:
            dirty_fields.append(key)
    return dirty_fields


def _default_tianxing_observation():
    return {
        "last_observed_at": 0,
        "last_action": "",
        "last_result": "",
        "last_summary": "",
        "last_error": "",
        "available_stars": [],
        "fixed_star": "",
        "current_prediction": "",
        "current_prediction_until": 0,
        "current_change": "",
        "current_change_until": 0,
        "tianji_value": 0,
        "calamity_count": 0,
        "hit_count": 0,
        "miss_count": 0,
        "change_count": 0,
        "last_route": "",
        "last_star_effect": "",
        "last_tianji_gain": 0,
        "last_contrib_gain": 0,
        "last_bonus_gain": 0,
        "auto_next_time": 0,
        "auto_last_action": "",
        "auto_last_error": "",
        "recent": [],
    }


def normalize_tianxing_observation(value=None):
    observed = copy.deepcopy(_default_tianxing_observation())
    if isinstance(value, dict):
        observed.update(value)
    if not isinstance(observed.get("available_stars"), list):
        observed["available_stars"] = []
    observed["available_stars"] = [str(item) for item in observed.get("available_stars") or [] if str(item or "").strip()]
    if not isinstance(observed.get("recent"), list):
        observed["recent"] = []
    recent = []
    for item in observed.get("recent", []):
        if not isinstance(item, dict):
            continue
        entry = dict(item)
        entry["ts"], _dirty = _parse_observation_float(entry.get("ts", 0))
        recent.append(entry)
    observed["recent"] = recent[-8:]
    for key in TIANXING_OBSERVATION_TIME_KEYS:
        observed[key], _dirty = _parse_observation_float(observed.get(key, 0))
    for key in ("tianji_value", "calamity_count", "hit_count", "miss_count", "change_count", "last_tianji_gain", "last_contrib_gain", "last_bonus_gain"):
        try:
            observed[key] = int(observed.get(key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            observed[key] = 0
    return observed


def _short_summary(text, limit=80):
    raw_text = " / ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return raw_text[: int(limit or 80)]


def _stars_from_line(line):
    return [item.strip() for item in RE_BRACKET.findall(str(line or "")) if item.strip()]


def _wait_until(text, now, fallback=0):
    if has_wait_time(text):
        wait_sec = parse_wait_time(text)
        if wait_sec > 0:
            return float(now + wait_sec + TIANXING_TIME_BUFFER_SEC)
    if fallback:
        return float(now + fallback + TIANXING_TIME_BUFFER_SEC)
    return 0


def _parse_route_timer(value, now):
    raw = str(value or "").strip()
    if not raw or raw == "无":
        return "", 0
    route = raw.split("（", 1)[0].strip()
    return route, _wait_until(raw, now)


def looks_like_tianxing_text(text):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    if "逆天改命之物" in raw_text:
        return False
    if any(keyword in raw_text for keyword in ("【天星宗玩法帮助】", "【天机盘】", "【观命结果】")):
        return True
    if "司命盘" in raw_text and any(keyword in raw_text for keyword in ("推命", "改命", "命星", "命轨")):
        return True
    if any(keyword in raw_text for keyword in ("【推命命中】", "【推命落空】", "【天星偏转】", "【改命待发】", "【改命回天】")):
        return True
    if RE_STAR_EFFECT.search(raw_text):
        return True
    if "因【天星宗】灵脉加持" in raw_text:
        return True
    if "【天星宗】的观星长老" in raw_text:
        return True
    if "你所属的宗门: 【天星宗】" in raw_text and "司命盘要诀" in raw_text:
        return True
    if "成功化去 1 层逆命劫" in raw_text:
        return True
    return False


def parse_tianxing_text(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    raw_text = str(text or "").strip()
    if not raw_text:
        return None

    parsed = {
        "action": "",
        "result": "",
        "summary": "",
        "last_error": "",
    }

    if "【天星宗玩法帮助】" in raw_text:
        parsed.update(action="玩法帮助", result="guide", summary="天星宗玩法帮助")
        return parsed

    if "你所属的宗门: 【天星宗】" in raw_text and "司命盘要诀" in raw_text:
        parsed.update(action="宗门信息", result="guide", summary="天星宗宗门信息与司命盘要诀")
        return parsed

    if "【天星宗】的观星长老" in raw_text:
        parsed.update(action="拜入天星宗", result="not_qualified", summary="资质不足，未能拜入天星宗", last_error="无法感应九天星辰之力")
        return parsed

    if "【观命结果】" in raw_text:
        stars = []
        for line in raw_text.splitlines():
            if line.strip().startswith("【") and " - " in line:
                stars.extend(_stars_from_line(line))
        parsed.update(action="观命", result="success", summary="观命结果", available_stars=stars)
        return parsed

    if "【天机盘】" in raw_text:
        parsed.update(action="天机盘", result="panel", summary="天机盘状态")
        for line in raw_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("今日可选命星"):
                parsed["available_stars"] = _stars_from_line(stripped)
            elif stripped.startswith("今日已定命星"):
                stars = _stars_from_line(stripped)
                parsed["fixed_star"] = stars[0] if stars else ""
            elif stripped.startswith("当前推命"):
                value = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
                route, until = _parse_route_timer(value, now)
                parsed["current_prediction"] = route
                parsed["current_prediction_until"] = until
            elif stripped.startswith("当前改命"):
                value = stripped.split(":", 1)[-1].strip() if ":" in stripped else stripped.split("：", 1)[-1].strip()
                route, until = _parse_route_timer(value, now)
                parsed["current_change"] = route
                parsed["current_change_until"] = until
        tianji_match = RE_TIANJI_VALUE.search(raw_text)
        calamity_match = RE_CALAMITY.search(raw_text)
        counts_match = RE_COUNTS.search(raw_text)
        if tianji_match:
            parsed["tianji_value"] = int(tianji_match.group("value") or 0)
        if calamity_match:
            parsed["calamity_count"] = int(calamity_match.group("value") or 0)
        if counts_match:
            parsed["hit_count"] = int(counts_match.group("hit") or 0)
            parsed["miss_count"] = int(counts_match.group("miss") or 0)
            parsed["change_count"] = int(counts_match.group("change") or 0)
        return parsed

    set_match = RE_SET_STAR.search(raw_text)
    if set_match:
        star = set_match.group("star").strip()
        parsed.update(action="定命", result="success", summary=f"定命 {star}", fixed_star=star)
        return parsed

    predict_match = RE_PREDICT.search(raw_text)
    if predict_match and "推下一段命数" in raw_text:
        route = predict_match.group("route").strip()
        parsed.update(
            action="推命",
            result="success",
            summary=f"推命 {route}",
            last_route=route,
            current_prediction=route,
            current_prediction_until=_wait_until(raw_text, now, TIANXING_PREDICTION_SEC),
        )
        return parsed

    if "你已有一道关于" in raw_text and "推命尚未应验" in raw_text:
        route = (_stars_from_line(raw_text) or [""])[0]
        parsed.update(
            action="推命",
            result="cooldown",
            summary=f"推命尚未应验 {route}".strip(),
            last_route=route,
            current_prediction=route,
            current_prediction_until=_wait_until(raw_text, now),
            last_error="推命尚未应验",
        )
        return parsed

    change_match = RE_CHANGE_FATE.search(raw_text)
    if change_match:
        route = change_match.group("route").strip()
        parsed.update(
            action="改命",
            result="success",
            summary=f"改命 {route}",
            last_route=route,
            current_change=route,
            current_change_until=_wait_until(raw_text, now, TIANXING_CHANGE_FATE_SEC),
        )
        return parsed

    if "成功化去 1 层逆命劫" in raw_text:
        parsed.update(action="消劫", result="success", summary="成功化去 1 层逆命劫")
        return parsed

    if "因【天星宗】灵脉加持" in raw_text:
        bonus_match = RE_BONUS_GAIN.search(raw_text)
        parsed.update(action="闭关", result="success", summary="闭关成功，天星宗灵脉加持")
        if bonus_match:
            parsed["last_bonus_gain"] = int(bonus_match.group("gain") or 0)
        return parsed

    if (
        "【推命命中】" in raw_text
        or "【推命落空】" in raw_text
        or "【天星偏转】" in raw_text
        or "【改命待发】" in raw_text
        or "【改命回天】" in raw_text
        or RE_STAR_EFFECT.search(raw_text)
    ):
        result = "modifier"
        if "【改命回天】" in raw_text:
            result = "change_triggered"
        elif "【推命命中】" in raw_text:
            result = "prediction_hit"
        elif "【推命落空】" in raw_text:
            result = "prediction_miss"
        star_effect = ""
        star_match = RE_STAR_EFFECT.search(raw_text)
        if star_match:
            star_effect = f"{star_match.group('star').strip()} {star_match.group('desc').strip()}".strip()
        parsed.update(action="命盘偏转", result=result, summary=_short_summary(raw_text), last_star_effect=star_effect)
        tianji_gain_match = RE_TIANJI_GAIN.search(raw_text)
        contrib_gain_match = RE_CONTRIB_GAIN.search(raw_text)
        calamity_gain_match = RE_CALAMITY_GAIN.search(raw_text)
        if tianji_gain_match:
            parsed["last_tianji_gain"] = int(tianji_gain_match.group("gain") or 0)
        if contrib_gain_match:
            parsed["last_contrib_gain"] = int(contrib_gain_match.group("gain") or 0)
        if calamity_gain_match:
            parsed["calamity_delta"] = int(calamity_gain_match.group("gain") or 0)
        if "【推命命中】" in raw_text or "【推命落空】" in raw_text:
            parsed["current_prediction"] = ""
            parsed["current_prediction_until"] = 0
        if "【改命回天】" in raw_text:
            parsed["current_change"] = ""
            parsed["current_change_until"] = 0
        elif "【改命待发】" in raw_text:
            parsed["current_change_until"] = _wait_until(raw_text, now)
        return parsed

    if not looks_like_tianxing_text(raw_text):
        return None
    parsed.update(action="未知天星宗文案", result="observed", summary=_short_summary(raw_text))
    return parsed


def apply_tianxing_passive(text, now=None, family=""):
    now = float(now if now is not None else time.time())
    parsed = parse_tianxing_text(text, now=now, family=family)
    if not parsed:
        return False

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    observed["last_observed_at"] = now
    for key in ("last_action", "last_result", "last_summary", "last_error", "fixed_star", "current_prediction", "current_change", "last_route", "last_star_effect"):
        source_key = key
        if key == "last_action":
            source_key = "action"
        elif key == "last_result":
            source_key = "result"
        elif key == "last_summary":
            source_key = "summary"
        value = parsed.get(source_key)
        if value is not None:
            observed[key] = value
    if parsed.get("available_stars") is not None:
        observed["available_stars"] = list(parsed.get("available_stars") or [])
    for key in ("current_prediction_until", "current_change_until"):
        if key in parsed:
            observed[key] = float(parsed.get(key) or 0)
    for key in ("tianji_value", "calamity_count", "hit_count", "miss_count", "change_count", "last_tianji_gain", "last_contrib_gain", "last_bonus_gain"):
        if parsed.get(key) is not None:
            observed[key] = int(parsed.get(key) or 0)
    if parsed.get("calamity_delta") is not None:
        observed["calamity_count"] = max(0, int(observed.get("calamity_count", 0) or 0) + int(parsed.get("calamity_delta") or 0))
    if parsed.get("action") == "消劫" and parsed.get("result") == "success":
        observed["calamity_count"] = max(0, int(observed.get("calamity_count", 0) or 0) - 1)
    observed["auto_last_error"] = ""
    if int(observed.get("calamity_count", 0) or 0) > 0:
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    elif not observed.get("fixed_star") and not observed.get("available_stars"):
        observed["auto_next_time"] = min(float(observed.get("auto_next_time", 0) or 0) or now + 60, now + 60)
    else:
        observed["auto_next_time"] = max(float(observed.get("auto_next_time", 0) or 0), now + TIANXING_AUTO_STATUS_BACKOFF_SEC)
    observed["recent"].append({
        "ts": now,
        "action": observed.get("last_action", ""),
        "result": observed.get("last_result", ""),
        "summary": observed.get("last_summary", ""),
    })
    observed["recent"] = observed["recent"][-8:]
    state["tianxing_observation"] = observed
    return True


def _normalize_manual_action(action):
    raw = str(action or "").strip().lower()
    mapping = {
        "": "panel",
        "panel": "panel",
        "盘": "panel",
        "查盘": "panel",
        "天机盘": "panel",
        "observe": "observe",
        "观命": "observe",
        "set": "set_star",
        "star": "set_star",
        "set_star": "set_star",
        "定命": "set_star",
        "predict": "predict",
        "推命": "predict",
        "change": "change_fate",
        "change_fate": "change_fate",
        "改命": "change_fate",
        "clear": "clear_calamity",
        "clear_calamity": "clear_calamity",
        "消劫": "clear_calamity",
    }
    return mapping.get(raw, raw)


def _manual_block(action, reason, command="", family=""):
    return {
        "allowed": False,
        "action": action,
        "command": command,
        "family": family,
        "reason": reason,
    }


def _manual_allow(action, command, family, now):
    return {
        "allowed": True,
        "action": action,
        "command": command,
        "family": family,
        "reason": "天星宗手动动作允许发送。",
        "source_module": "天星宗",
        "op_id": f"tianxing-{action}-{int(now)}",
        "delete_policy": "manual_keep",
        "max_retry": 0,
    }


def _has_recent_observation(observed, now):
    last_observed_at = float(observed.get("last_observed_at", 0) or 0)
    return last_observed_at > 0 and now - last_observed_at <= TIANXING_OBSERVATION_STALE_SEC


def build_tianxing_manual_plan(action="panel", arg="", now=None):
    now = float(now if now is not None else time.time())
    action = _normalize_manual_action(action)
    arg = str(arg or "").strip()
    if not state.get("tianxing_enabled"):
        return _manual_block(action, "天星宗模块未开启。")

    if action == "panel":
        return _manual_allow(action, CMD_TIANXING_PANEL, "tianxing_panel", now)
    if action == "observe":
        return _manual_allow(action, CMD_TIANXING_OBSERVE, "tianxing_observe", now)

    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        return _manual_block(action, f"天星宗状态字段异常（{_format_list(dirty_fields)}），不猜测冷却或待办时间。")

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    last_observed_at = float(observed.get("last_observed_at", 0) or 0)
    if last_observed_at <= 0:
        return _manual_block(action, "缺少天星宗真实文案状态，先手动观命/查盘或等待消息盒子观察。")
    if now - last_observed_at > TIANXING_OBSERVATION_STALE_SEC:
        return _manual_block(action, f"天星宗状态过旧，最近观察 {fmt_abs_ts(last_observed_at)}。")

    if action == "set_star":
        star = arg
        if star not in TIANXING_STARS:
            return _manual_block(action, "定命必须指定：紫微、天府、太阴、贪狼。")
        fixed_star = str(observed.get("fixed_star") or "").strip()
        if fixed_star:
            return _manual_block(action, f"今日已定命星：{fixed_star}，不重复定命。")
        available_stars = [str(item) for item in observed.get("available_stars") or [] if str(item or "").strip()]
        if available_stars and star not in available_stars:
            return _manual_block(action, f"今日可选命星为 {_format_list(available_stars)}，不发送定命 {star}。")
        if not available_stars:
            return _manual_block(action, "未记录今日可选命星，先手动观命。")
        return _manual_allow(action, f"{CMD_TIANXING_SET_STAR} {star}", "tianxing_set_star", now)

    if action == "predict":
        route = arg
        if route not in TIANXING_ROUTES:
            return _manual_block(action, "推命必须指定：闭关、炼制、探索、斗法。")
        prediction_until = float(observed.get("current_prediction_until", 0) or 0)
        if prediction_until > now:
            current = observed.get("current_prediction") or "未记录"
            return _manual_block(action, f"已有推命 {current} 尚未应验，{fmt_remaining(prediction_until)} 后再试。")
        if str(observed.get("current_prediction") or "").strip() and prediction_until <= 0:
            current = observed.get("current_prediction") or "未记录"
            return _manual_block(action, f"已有推命 {current} 尚未应验，但时间不可解析，不发送推命。")
        return _manual_allow(action, f"{CMD_TIANXING_PREDICT} {route}", "tianxing_predict", now)

    if action == "change_fate":
        route = arg
        if route not in TIANXING_ROUTES:
            return _manual_block(action, "改命必须指定：闭关、炼制、探索、斗法。")
        change_until = float(observed.get("current_change_until", 0) or 0)
        if change_until > now:
            current = observed.get("current_change") or "未记录"
            return _manual_block(action, f"已有改命 {current} 尚未触发，{fmt_remaining(change_until)} 后再试。")
        if str(observed.get("current_change") or "").strip() and change_until <= 0:
            current = observed.get("current_change") or "未记录"
            return _manual_block(action, f"已有改命 {current} 尚未触发，但时间不可解析，不发送改命。")
        if int(observed.get("tianji_value", 0) or 0) < 3:
            return _manual_block(action, f"天机值不足 3，当前记录为 {int(observed.get('tianji_value', 0) or 0)}。")
        return _manual_allow(action, f"{CMD_TIANXING_CHANGE_FATE} {route}", "tianxing_change_fate", now)

    if action == "clear_calamity":
        if int(observed.get("calamity_count", 0) or 0) <= 0:
            return _manual_block(action, "当前未记录逆命劫，不发送消劫。")
        return _manual_allow(action, CMD_TIANXING_CLEAR_CALAMITY, "tianxing_clear_calamity", now)

    return _manual_block(action, "未知天星宗手动动作。")


def _set_tianxing_auto_wait(observed, now, action, next_time=None, error=""):
    observed["auto_last_action"] = str(action or "")
    observed["auto_last_error"] = str(error or "")
    observed["auto_next_time"] = float(next_time or now + TIANXING_AUTO_BLOCK_BACKOFF_SEC)
    state["tianxing_observation"] = observed
    save_state()


async def run_tianxing_scheduler(now):
    now = float(now if now is not None else time.time())
    if not state.get("tianxing_enabled"):
        return

    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    if dirty_fields:
        return

    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    if auto_next_time > 0 and now < auto_next_time:
        return

    if not _has_recent_observation(observed, now):
        plan = build_tianxing_manual_plan("panel", now=now)
    elif int(observed.get("calamity_count", 0) or 0) > 0:
        plan = build_tianxing_manual_plan("clear_calamity", now=now)
    elif not observed.get("fixed_star") and not observed.get("available_stars"):
        plan = build_tianxing_manual_plan("observe", now=now)
    else:
        _set_tianxing_auto_wait(observed, now, "idle", now + TIANXING_AUTO_STATUS_BACKOFF_SEC)
        return

    action = str(plan.get("action") or "")
    if not plan.get("allowed"):
        _set_tianxing_auto_wait(
            observed,
            now,
            action,
            now + TIANXING_AUTO_BLOCK_BACKOFF_SEC,
            plan.get("reason") or "天星宗自动调度未满足条件",
        )
        return

    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=0,
        priority="normal",
        source_module="天星宗",
        op_id=f"tianxing-auto-{action}-{int(now)}",
    )
    sent_at = now
    if msg:
        parsed_sent_at, sent_at_dirty = _parse_observation_float(getattr(msg, "sent_at", 0))
        sent_at = now if sent_at_dirty or parsed_sent_at <= 0 else parsed_sent_at
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    if not msg:
        _set_tianxing_auto_wait(
            observed,
            now,
            action,
            sent_at + TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC,
            "天星宗自动调度发送失败或被安全策略拦截",
        )
        return

    observed["auto_last_action"] = action
    observed["auto_last_error"] = ""
    observed["auto_next_time"] = sent_at + TIANXING_AUTO_STATUS_BACKOFF_SEC
    state["tianxing_observation"] = observed
    save_state()


async def execute_tianxing_manual_action(action="panel", arg="", *, send_as_id=None, now=None):
    now = float(now if now is not None else time.time())
    if send_as_id is not None:
        with use_identity(send_as_id):
            plan = build_tianxing_manual_plan(action, arg, now=now)
    else:
        plan = build_tianxing_manual_plan(action, arg, now=now)
    if not plan.get("allowed"):
        return False, plan.get("reason") or "天星宗动作未允许", plan
    msg = await send_game_command(
        plan["command"],
        track=True,
        max_retry=int(plan.get("max_retry", 0) or 0),
        send_as_id=send_as_id,
        priority="normal",
        source_module=plan.get("source_module") or "天星宗",
        op_id=plan.get("op_id") or "",
        delete_policy=plan.get("delete_policy") or "manual_keep",
    )
    if not msg:
        return False, "发送被运行时安全策略拦截或账号不可用。", plan
    return True, f"已发送：{plan['command']}（msg_id={int(getattr(msg, 'id', 0) or 0)}）", plan


def _format_list(values):
    return "、".join(str(item) for item in values if str(item or "").strip()) or "未记录"


def get_tianxing_status_text():
    if not state.get("tianxing_enabled"):
        return "\n".join([
            "🌌 天星宗",
            "- 模块：关闭（不会主动发送）",
            "- 运行快照：关闭时不展示旧观察记录",
        ])
    dirty_fields = _dirty_tianxing_time_fields(state.get("tianxing_observation"))
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    lines = [
        "🌌 天星宗",
        f"- 模块：{'开启' if state.get('tianxing_enabled') else '关闭'}（被动观察，手动动作受控发送）",
        "- 命令：.观命｜.定命 <紫微|天府|太阴|贪狼>｜.推命/.改命 <闭关|炼制|探索|斗法>｜.天机盘｜.消劫",
        f"- 命星：可选 {_format_list(observed.get('available_stars'))}｜已定 {observed.get('fixed_star') or '未记录'}",
        f"- 推命：{observed.get('current_prediction') or '无'}｜{fmt_abs_ts(observed.get('current_prediction_until', 0))}（{fmt_remaining(observed.get('current_prediction_until', 0))}）",
        f"- 改命：{observed.get('current_change') or '无'}｜{fmt_abs_ts(observed.get('current_change_until', 0))}（{fmt_remaining(observed.get('current_change_until', 0))}）",
        f"- 天机/逆命劫：{observed.get('tianji_value', 0)} / {observed.get('calamity_count', 0)}",
        f"- 命中/落空/改命：{observed.get('hit_count', 0)} / {observed.get('miss_count', 0)} / {observed.get('change_count', 0)}",
        f"- 最近动作：{observed.get('last_action') or '未记录'} / {observed.get('last_result') or '未记录'}",
        f"- 最近观察：{fmt_abs_ts(observed.get('last_observed_at', 0))}",
        f"- 自动调度：{fmt_abs_ts(observed.get('auto_next_time', 0))}（{fmt_remaining(observed.get('auto_next_time', 0))}）",
    ]
    if dirty_fields:
        lines.append(f"- 状态异常：{_format_list(dirty_fields)} 不可解析，自动发送已暂停")
    if observed.get("last_star_effect"):
        lines.append(f"- 命盘照命：{observed.get('last_star_effect')}")
    if observed.get("last_tianji_gain") or observed.get("last_contrib_gain"):
        lines.append(f"- 最近收益：天机+{observed.get('last_tianji_gain', 0)}｜贡献+{observed.get('last_contrib_gain', 0)}")
    if observed.get("last_bonus_gain"):
        lines.append(f"- 闭关加成：修为+{observed.get('last_bonus_gain')}")
    if observed.get("last_error"):
        lines.append(f"- 异常：{observed.get('last_error')}")
    if observed.get("auto_last_error"):
        lines.append(f"- 自动异常：{observed.get('auto_last_error')}")
    recent = observed.get("recent") or []
    if recent:
        lines.append("- 最近事件：")
        for item in recent[-3:]:
            lines.append(f"  {fmt_abs_ts(item.get('ts', 0))} {item.get('action') or '-'} {item.get('result') or '-'}")
    return "\n".join(lines)


__all__ = [
    "apply_tianxing_passive",
    "build_tianxing_manual_plan",
    "execute_tianxing_manual_action",
    "get_tianxing_status_text",
    "looks_like_tianxing_text",
    "normalize_tianxing_observation",
    "parse_tianxing_text",
    "run_tianxing_scheduler",
]
