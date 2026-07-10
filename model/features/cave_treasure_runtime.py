import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

from ..inventory_delta import record_inventory_delta, stable_payload_digest
from ..miniapp_state import record_miniapp_state
from ..persistence import save_state
from ..runtime import send_audit_log
from ..state import get_current_identity_id, get_global_enabled, get_global_pause_source, get_identity_account, get_identity_enabled, get_send_as_profile, state, use_identity
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from . import deep_retreat, yuanying
from .cave_treasure_miniapp import (
    build_cave_treasure_launch_args,
    extract_cave_treasure_miniapp_launch,
    request_cave_treasure_miniapp_init_data,
    run_cave_deep_seclusion_action_production_flow,
    run_cave_dwelling_start_production_flow,
    run_cave_external_action_production_flow,
    run_cave_small_world_production_flow,
    run_cave_tianjige_command_production_flow,
    run_cave_treasure_miniapp_production_flow,
)
from .trial_miniapp import build_trial_launch_args
from .trial_runtime import _format_trial_summary, _trial_miniapp_capture_store, run_trial_miniapp_production_flow


CAVE_TREASURE_MANUAL_AUTH_TTL_SEC = 10 * 60
CAVE_TREASURE_MANUAL_MAX_STEPS = 48
CAVE_TREASURE_MINIAPP_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "miniapp_capture"
CAVE_SMALL_WORLD_RESOURCE_PAUSE_SEC = 6 * 3600
CAVE_SMALL_WORLD_CYCLE_SEC = 6 * 3600
CAVE_SMALL_WORLD_REFRESH_SEC = 10 * 60
CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS = 5

_MANUAL_AUTH_UNTIL = {}
_RUN_LOCKS = {}
_PUBLIC_ENTRY_LOCKS = {}
_GAIN_KEYS = {
    "expgain": "经验",
    "experiencegain": "经验",
    "cultivationgain": "修为",
    "xiuweigain": "修为",
    "lingshigain": "灵石",
    "spiritstonegain": "灵石",
    "stonegain": "灵石",
    "contribution": "贡献",
}
_REWARD_CONTAINER_KEYS = {"rewards", "reward", "bonusloot", "loot", "drops", "items", "materials", "gains"}
_LOG_KEYS = {"logs", "log"}
_TECHNICAL_KEYS = {
    "score",
    "session",
    "sessionid",
    "ready",
    "rounds",
    "qualitybonus",
    "status",
    "phase",
    "mode",
    "step",
    "steps",
    "events",
    "proof",
}


def _miniapp_http_allowed_during_pause():
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"
_ITEM_TEXT_RE = re.compile(
    r"(?:获得|奖励|收获|掉落|战利品|材料)?\s*(?:【(?P<bracket>[^】]+)】|(?P<plain>[\u4e00-\u9fffA-Za-z0-9_·-]{2,24}))\s*[xX×]\s*(?P<count>[\d,]+)"
)
_GAIN_TEXT_RE = re.compile(r"(?P<name>修为|经验|灵石|天机残痕)\s*[+＋]\s*(?P<count>[\d,]+)")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")
_INVENTORY_GAIN_NAMES = {"灵石"}
_CAVE_TREASURE_STATE_OUTPUTS = ("module_snapshot", "daily_counter", "inventory_delta")


def _identity_id(value=None):
    try:
        return int(value if value is not None else get_current_identity_id() or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def authorize_cave_treasure_miniapp_manual_run(identity_id, *, now=None, ttl_sec=CAVE_TREASURE_MANUAL_AUTH_TTL_SEC):
    identity_id = _identity_id(identity_id)
    if identity_id <= 0:
        return 0
    now = float(now or time.time())
    _MANUAL_AUTH_UNTIL[identity_id] = now + max(30, float(ttl_sec or CAVE_TREASURE_MANUAL_AUTH_TTL_SEC))
    return _MANUAL_AUTH_UNTIL[identity_id]


def revoke_cave_treasure_miniapp_manual_run(identity_id):
    _MANUAL_AUTH_UNTIL.pop(_identity_id(identity_id), None)


def _has_manual_auth(identity_id, now):
    identity_id = _identity_id(identity_id)
    expires_at = float(_MANUAL_AUTH_UNTIL.get(identity_id, 0) or 0)
    if expires_at <= 0:
        return False
    if float(now or time.time()) > expires_at:
        _MANUAL_AUTH_UNTIL.pop(identity_id, None)
        return False
    return True


def _run_lock(identity_id):
    identity_id = _identity_id(identity_id)
    lock = _RUN_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _RUN_LOCKS[identity_id] = lock
    return lock


def _public_entry_lock(identity_id):
    identity_id = _identity_id(identity_id)
    lock = _PUBLIC_ENTRY_LOCKS.get(identity_id)
    if lock is None:
        lock = asyncio.Lock()
        _PUBLIC_ENTRY_LOCKS[identity_id] = lock
    return lock


def _public_entry_allowed():
    return get_global_enabled() or get_global_pause_source() == "tianzun_maintenance"


def _parse_public_cave_entry_url(public_entry_url):
    launch, _args = build_cave_treasure_launch_args(str(public_entry_url or "").strip())
    if not launch.allowed or not launch.start_param:
        return "", "", launch.reason or "invalid cave public entry"
    return launch.start_param, launch.webview_url, ""


def _public_entry_account_identity_error(identity_id):
    """A WebApp login represents its physical Telegram account, not a send-as peer."""
    identity_id = _identity_id(identity_id)
    try:
        account_id = int(get_identity_account(identity_id) or 0)
    except (TypeError, ValueError, OverflowError):
        account_id = 0
    if account_id > 0 and account_id != identity_id:
        return "洞府公共入口绑定登录账号，请选择该账号本体身份后执行"
    return ""


def _iter_dicts(value, *, depth=0):
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child, depth=depth + 1)


def _find_trial_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
        if not url:
            continue
        if url.startswith("/"):
            url = urljoin("https://asc.aiopenai.app/", url)
        launch, _args = build_trial_launch_args(url)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": launch.webview_url,
                "title": str(item.get("title") or item.get("buttonText") or item.get("key") or "").strip(),
                "safe_summary": launch.safe_summary(),
            }
    return {}


def _find_trial_external_app_in_cave_payload(value):
    root = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else value
    account = root.get("account") if isinstance(root, dict) and isinstance(root.get("account"), dict) else {}
    external = account.get("externalApps") if isinstance(account.get("externalApps"), dict) else {}
    for group in external.get("groups") or ():
        if not isinstance(group, dict):
            continue
        for item in group.get("apps") or ():
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip().lower()
            title = str(item.get("title") or item.get("buttonText") or "").strip()
            action = str(item.get("action") or "").strip().lower()
            url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
            is_trial = key in {"trial", "tianji_trial"} or "天机试炼" in title or "xianxia-trial" in url
            if not is_trial:
                continue
            normalized_action = action if action in {"trial", "tianji_trial"} else ""
            if not normalized_action and key == "tianji_trial" and url in {"", "#"}:
                normalized_action = "tianji_trial"
            return {
                "action": normalized_action,
                "url": url,
                "title": title or key,
                "available": bool(item.get("available", True)),
            }
    return {}


def _entry_mentions_current_identity(text):
    usernames = {
        str(match.group(1) or "").strip().lower()
        for match in _MENTION_RE.finditer(str(text or ""))
    }
    usernames.discard("")
    if not usernames:
        return False
    profile_username = str((get_send_as_profile() or {}).get("username") or "").strip().lstrip("@").lower()
    return bool(profile_username and profile_username in usernames)


def _normalize_key(key):
    return re.sub(r"[^A-Za-z0-9]", "", str(key or "")).lower()


def _parse_int(value, default=0):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError, OverflowError):
        return default


def _reward_from_value(value, *, fallback_name=""):
    if isinstance(value, str):
        name = value.strip()
        return {"name": name, "qty": 1} if name else {}
    if isinstance(value, (int, float)) and fallback_name:
        qty = _parse_int(value, 0)
        return {"name": str(fallback_name).strip(), "qty": qty} if qty > 0 else {}
    if not isinstance(value, dict):
        return {}
    name = ""
    for key in ("name", "itemName", "item_name", "title", "label"):
        if value.get(key) not in (None, ""):
            name = str(value.get(key) or "").strip()
            break
    if not name and fallback_name:
        name = str(fallback_name).strip()
    if not name:
        return {}
    qty = value.get("qty", value.get("count", value.get("quantity", value.get("amount", 1))))
    return {"name": name, "qty": max(1, _parse_int(qty, 1))}


def _rewards_from_container(value):
    rewards = []
    if isinstance(value, list):
        for item in value:
            reward = _reward_from_value(item)
            if reward:
                rewards.append(reward)
        return rewards
    if isinstance(value, dict):
        direct = _reward_from_value(value)
        if direct:
            return [direct]
        for name, amount in value.items():
            reward = _reward_from_value(amount, fallback_name=name)
            if reward:
                rewards.append(reward)
    return rewards


def _merge_reward_counts(target, rewards):
    for reward in rewards or ():
        if not isinstance(reward, dict):
            continue
        name = str(reward.get("name") or "").strip()
        if not name:
            continue
        target[name] = int(target.get(name, 0) or 0) + max(1, _parse_int(reward.get("qty"), 1))


def _collect_from_text(text, *, rewards, gains):
    text = str(text or "")
    for match in _ITEM_TEXT_RE.finditer(text):
        name = str(match.group("bracket") or match.group("plain") or "").strip(" ：:，,。")
        name = re.sub(r"^(?:获得|奖励|收获|掉落|战利品|材料)", "", name).strip(" ：:，,。")
        qty = _parse_int(match.group("count"), 0)
        if name and qty > 0 and name not in {"神识", "游戏", "次数"}:
            rewards[name] = int(rewards.get(name, 0) or 0) + qty
    for match in _GAIN_TEXT_RE.finditer(text):
        name = str(match.group("name") or "").strip()
        amount = _parse_int(match.group("count"), 0)
        if name and amount > 0:
            gains[name] = int(gains.get(name, 0) or 0) + amount


def _collect_materials(value, *, rewards=None, gains=None, depth=0):
    rewards = rewards if rewards is not None else {}
    gains = gains if gains is not None else {}
    if depth > 5:
        return rewards, gains
    if isinstance(value, str):
        _collect_from_text(value, rewards=rewards, gains=gains)
        return rewards, gains
    if isinstance(value, list):
        for item in value:
            _collect_materials(item, rewards=rewards, gains=gains, depth=depth + 1)
        return rewards, gains
    if not isinstance(value, dict):
        return rewards, gains
    for key, child in value.items():
        normalized = _normalize_key(key)
        if normalized in _TECHNICAL_KEYS:
            continue
        if normalized in _LOG_KEYS:
            log_rewards, log_gains = _collect_materials(child, rewards={}, gains={}, depth=depth + 1)
            for name, qty in log_rewards.items():
                rewards[name] = max(int(rewards.get(name, 0) or 0), int(qty or 0))
            for name, amount in log_gains.items():
                gains[name] = max(int(gains.get(name, 0) or 0), int(amount or 0))
            continue
        if normalized in _REWARD_CONTAINER_KEYS:
            _merge_reward_counts(rewards, _rewards_from_container(child))
            _collect_materials(child, rewards=rewards, gains=gains, depth=depth + 1)
            continue
        gain_label = _GAIN_KEYS.get(normalized)
        if gain_label:
            amount = _parse_int(child, 0)
            if amount > 0:
                gains[gain_label] = int(gains.get(gain_label, 0) or 0) + amount
            continue
        _collect_materials(child, rewards=rewards, gains=gains, depth=depth + 1)
    return rewards, gains


def _format_material_summary(data):
    rewards, gains = _collect_materials(data or {})
    parts = []
    if gains:
        parts.append("收益:" + "、".join(f"{name}+{amount}" for name, amount in sorted(gains.items()) if amount > 0))
    if rewards:
        parts.append("奖励:" + "、".join(f"{name}x{amount}" for name, amount in sorted(rewards.items()) if amount > 0))
    return "｜".join(parts)


def _format_cave_treasure_summary(result):
    result = dict(result or {})
    status = str(result.get("status") or "unknown").strip() or "unknown"
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    games = ""
    if state:
        games_used = _parse_int(state.get("games_used"), 0)
        games_limit = _parse_int(state.get("games_limit"), 0)
        if games_limit > 0:
            games = f"｜游戏 {games_used}/{games_limit}"
    if result.get("ok"):
        material_text = _format_material_summary(data)
        return f"MiniApp {status}{games}｜{material_text or '未解析到新增物资'}"
    error = str(result.get("error") or "").strip()
    return f"MiniApp {status}{games}｜{error or '未完成'}"


def _iter_nested_dicts(value, *, depth=0):
    if depth > 5:
        return
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_nested_dicts(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_nested_dicts(child, depth=depth + 1)


def extract_cave_deep_seclusion_action_message(data):
    data = data if isinstance(data, dict) else {}
    for container_key in ("actionResult", "result", "data"):
        container = data.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in ("rawMessage", "raw_message", "message", "text", "statusText", "status_text"):
            text = str(container.get(key) or "").strip()
            if text:
                return text
    for item in _iter_nested_dicts(data or {}):
        for key in ("rawMessage", "raw_message", "message", "text", "statusText", "status_text"):
            text = str(item.get(key) or "").strip()
            if text:
                return text
    return ""


def _extract_cave_deep_seclusion_status_message(data):
    data = data if isinstance(data, dict) else {}
    for item in _iter_nested_dicts(data or {}):
        active = bool(item.get("active"))
        remaining = item.get("remainingSeconds", item.get("remaining_seconds", 0))
        try:
            remaining = int(float(remaining or 0))
        except (TypeError, ValueError, OverflowError):
            remaining = 0
        status_text = str(item.get("statusText") or item.get("status_text") or "").strip()
        if status_text and ("闭关" in status_text or active):
            return status_text
        if active and remaining > 0:
            return f"闭关中，剩余 {remaining} 秒。"
    return ""


def extract_cave_tianjige_command_message(data):
    """Extract the player-facing command-center reply without exposing request secrets."""
    return extract_cave_deep_seclusion_action_message(data)


def _cave_tianjige_action_succeeded(data):
    data = data if isinstance(data, dict) else {}
    action_result = data.get("actionResult") if isinstance(data.get("actionResult"), dict) else {}
    if "ok" in action_result:
        return bool(action_result.get("ok"))
    return True


async def sync_cave_tianjige_yuanying_result(identity_id, data, *, now):
    """Replay only safe Tianjige YuanYing outcomes into the existing state machine.

    The normal status handler can emit a legacy group command for `窍中温养`.
    A public-entry response must never trigger that side effect, so this bridge
    handles success and explicit cooldown wording only.
    """
    identity_id = _identity_id(identity_id)
    message = extract_cave_tianjige_command_message(data)
    if identity_id <= 0 or not message:
        return {"handled": False, "reason": "missing_identity_or_message", "message": "", "phase": ""}

    with use_identity(identity_id):
        reply_to = SimpleNamespace(raw_text=yuanying.CMD_YUANYING, id=0)
        handled = await yuanying.handle_yuanying_success_reply(
            message,
            now,
            reply_to=reply_to,
            matched_family="yuanying",
        )
        if handled:
            return {
                "handled": True,
                "reason": "",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        cooldown_hint = any(token in message for token in ("尚未恢复", "冷却", "等待", "不足", "休息", "归来倒计时"))
        if cooldown_hint and "窍中温养" not in message:
            handled = await yuanying.handle_yuanying_status_reply(
                message,
                now,
                reply_to=reply_to,
                matched_family="yuanying",
            )
        return {
            "handled": bool(handled),
            "reason": "" if handled else "unrecognized_or_nonterminal_message",
            "message": message,
            "phase": str(state.get("yuanying_phase") or ""),
        }


async def sync_cave_deep_seclusion_action_result(identity_id, action, data, *, now):
    """Replay a dwelling MiniApp deep-seclusion result through deep-retreat handlers."""

    identity_id = _identity_id(identity_id)
    action = str(action or "").strip()
    message = extract_cave_deep_seclusion_action_message(data)
    if action == "status" and not message:
        message = _extract_cave_deep_seclusion_status_message(data)
    if identity_id <= 0 or not message:
        return {"handled": False, "reason": "missing_identity_or_message", "message_kind": ""}

    with use_identity(identity_id):
        if action == "settle":
            if "深度闭关总结" not in message and "功成圆满" not in message:
                return {"handled": False, "reason": "not_summary_message", "message_kind": "other"}
            if state.get("deep_retreat_phase") not in ("summary_due", "observing_summary", "waiting_summary", "running"):
                deep_retreat.begin_deep_retreat_summary_wait(now)
            before = str(state.get("deep_retreat_phase") or "")
            await deep_retreat.handle_deep_retreat_summary_broadcast(
                message,
                now,
                reply_context={"send_as_id": identity_id, "family": "deep_retreat", "route_source": "cave_miniapp"},
            )
            after = str(state.get("deep_retreat_phase") or "")
            return {"handled": before != after or after == "post_summary_wait", "reason": "", "message_kind": "summary", "phase": after}

        if action == "start":
            reply_to = SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT, id=0)
            handled = await deep_retreat.handle_deep_retreat_success_reply(
                message,
                now,
                reply_to=reply_to,
                matched_family="deep_retreat",
            )
            if not handled:
                handled = await deep_retreat.handle_deep_retreat_running_reply(
                    message,
                    now,
                    reply_to=reply_to,
                    matched_family="deep_retreat",
                )
            return {
                "handled": bool(handled),
                "reason": "" if handled else "start_message_not_handled",
                "message_kind": "start",
                "phase": str(state.get("deep_retreat_phase") or ""),
            }

        if action == "status":
            reply_to = SimpleNamespace(raw_text=deep_retreat.CMD_DEEP_RETREAT_QUERY, id=0)
            handled = await deep_retreat.handle_deep_retreat_status_reply(
                message,
                now,
                reply_to=reply_to,
                matched_family="deep_retreat",
            )
            return {
                "handled": bool(handled),
                "reason": "" if handled else "status_message_not_handled",
                "message_kind": "status",
                "phase": str(state.get("deep_retreat_phase") or ""),
            }

    return {"handled": False, "reason": "unsupported_action", "message_kind": ""}


def _collect_session_ids(value, *, depth=0):
    if depth > 6:
        return []
    session_ids = []
    if isinstance(value, list):
        for item in value:
            session_ids.extend(_collect_session_ids(item, depth=depth + 1))
        return session_ids
    if not isinstance(value, dict):
        return session_ids
    for key, item in value.items():
        normalized = _normalize_key(key)
        if normalized in {"session", "sessionid", "huntsession", "huntsessionid"}:
            text = str(item or "").strip()
            if text:
                session_ids.append(text)
        elif isinstance(item, (dict, list)):
            session_ids.extend(_collect_session_ids(item, depth=depth + 1))
    return session_ids


def _cave_treasure_inventory_source_id(data, *, result_msg_id=0):
    data = data if isinstance(data, dict) else {}
    sessions = sorted(set(_collect_session_ids(data)))
    result_payload = data.get("results") or data.get("huntResult") or data
    result_digest = stable_payload_digest(result_payload)
    if sessions:
        return f"sessions:{stable_payload_digest(sessions)}:{result_digest}"
    if int(result_msg_id or 0) > 0:
        return f"msg:{int(result_msg_id or 0)}:{result_digest}"
    return f"payload:{result_digest}"


def _cave_treasure_inventory_items(result):
    result = dict(result or {})
    if not result.get("ok"):
        return {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    status = str(result.get("status") or "").strip()
    settled_count = _parse_int(result.get("settled_count") or data.get("settled_count"), 0)
    if status not in {"settled", "daily_limit"} and settled_count <= 0 and not data.get("results"):
        return {}
    rewards, gains = _collect_materials(data or {})
    items = dict(rewards)
    for name in _INVENTORY_GAIN_NAMES:
        amount = _parse_int(gains.get(name), 0)
        if amount > 0:
            items[name] = _parse_int(items.get(name), 0) + amount
    return {name: count for name, count in items.items() if str(name or "").strip() and _parse_int(count, 0) > 0}


def _record_cave_treasure_inventory_delta(identity_id, result, *, now, result_msg_id=0):
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    items = _cave_treasure_inventory_items(result)
    if not items:
        return {"changed": False, "record": {}, "record_key": ""}
    return record_inventory_delta(
        identity_id,
        source="cave_treasure_miniapp",
        source_id=_cave_treasure_inventory_source_id(data, result_msg_id=result_msg_id),
        items=items,
        now=now,
        source_summary={
            "status": (result or {}).get("status") or "",
            "settled_count": _parse_int((result or {}).get("settled_count") or data.get("settled_count"), 0),
            "result_msg_id": int(result_msg_id or 0),
        },
    )


def _cave_treasure_state_source_id(result, *, result_msg_id=0):
    result = dict(result or {})
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    digest = stable_payload_digest({
        "status": result.get("status") or "",
        "state": state,
        "settled_count": _parse_int(result.get("settled_count") or data.get("settled_count"), 0),
    })
    if int(result_msg_id or 0) > 0:
        return f"msg:{int(result_msg_id or 0)}:{digest}"
    return f"payload:{digest}"


def _record_cave_treasure_miniapp_state(identity_id, result, *, now, result_msg_id=0):
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    state = data.get("state") if isinstance(data.get("state"), dict) else {}
    if not state:
        return {"changed": False, "record": {}, "record_key": ""}
    return record_miniapp_state(
        identity_id,
        "cave_treasure",
        state,
        source="cave_treasure_miniapp",
        source_id=_cave_treasure_state_source_id(result, result_msg_id=result_msg_id),
        now=now,
        outputs=_CAVE_TREASURE_STATE_OUTPUTS,
        replaces_commands=(".洞府",),
    )


def _record_cave_small_world_state(identity_id, result, *, now, result_msg_id=0):
    data = dict((result or {}).get("data") or {})
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    small_world = overview.get("small_world") if isinstance(overview.get("small_world"), dict) else {}
    if not small_world:
        return {"changed": False, "record": {}, "record_key": ""}
    return record_miniapp_state(
        identity_id,
        "cave_small_world",
        small_world,
        source="cave_dwelling_miniapp",
        source_id=f"cave_small_world:{int(result_msg_id or 0)}:{stable_payload_digest(small_world)}",
        now=now,
        outputs=("module_snapshot",),
        replaces_commands=(".小世界",),
    )


def _cave_small_world_panel_snapshot(small_world, now):
    small_world = small_world if isinstance(small_world, dict) else {}
    missing = small_world.get("prayer_missing_resources") if isinstance(small_world.get("prayer_missing_resources"), list) else []
    manifest_cost = "、".join(
        f"{item.get('name') or '资源'}缺{int(item.get('missing', 0) or 0)}"
        for item in missing
        if isinstance(item, dict) and int(item.get("missing", 0) or 0) > 0
    )
    return {
        "temple_level": int(small_world.get("temple_level", 0) or 0),
        "temple_name": str(small_world.get("temple_name") or ""),
        "population": int(small_world.get("population", 0) or 0),
        "capacity": int(small_world.get("population_cap", 0) or 0),
        "faith": int(small_world.get("faith", 0) or 0),
        "faith_max": int(small_world.get("faith_cap", 100) or 100),
        "stability": int(small_world.get("stability", 0) or 0),
        "stability_max": int(small_world.get("stability_cap", 100) or 100),
        "pending_incense": float(small_world.get("pending_incense", 0) or 0),
        "stock": int(small_world.get("incense_stock", 0) or 0),
        "hourly_output": float(small_world.get("hourly_incense", 0) or 0),
        "barrier_status": "已开启" if small_world.get("barrier_active") else "未开启",
        "spiritual_strength": 0,
        "has_prayer": bool(small_world.get("has_prayer")),
        "prayer_name": str(small_world.get("prayer_title") or ""),
        "manifest_cost": manifest_cost,
        "has_wait": int(small_world.get("prayer_remaining_seconds", 0) or 0) > 0,
        "wait_sec": int(small_world.get("prayer_remaining_seconds", 0) or 0),
        "wait_text": "",
        "updated_at": float(now),
    }


def _apply_cave_small_world_overview(small_world, now):
    snapshot = _cave_small_world_panel_snapshot(small_world, now)
    state["small_world_last_panel_at"] = float(now)
    state["small_world_faith_value"] = int(snapshot.get("faith", 0) or 0)
    state["small_world_pending_incense"] = float(snapshot.get("pending_incense", 0) or 0)
    state["small_world_incense_stock"] = int(snapshot.get("stock", 0) or 0)
    state["small_world_panel_snapshot"] = snapshot
    return snapshot


def _plan_cave_public_small_world_action(overview):
    small_world = overview.get("small_world") if isinstance(overview, dict) and isinstance(overview.get("small_world"), dict) else {}
    if not small_world or not small_world.get("available") or not small_world.get("has_world"):
        return {"reason": "小世界尚不可用"}

    if small_world.get("has_prayer"):
        if not state.get("small_world_manifest_enabled"):
            return {"reason": "检测到祈愿，但自动显灵未开启"}
        if small_world.get("can_manifest") and small_world.get("prayer_resources_ready"):
            return {"action": "manifest", "reason": f"处理祈愿 {small_world.get('prayer_title') or '凡人祈愿'}"}
        missing = small_world.get("prayer_missing_resources") or []
        missing_text = "、".join(
            f"{item.get('name') or '资源'}缺{int(item.get('missing', 0) or 0)}"
            for item in missing
            if isinstance(item, dict)
        )
        return {"blocked": "resource", "reason": missing_text or "显灵资源不足或当前不可显灵"}

    if state.get("small_world_preach_enabled") and int(small_world.get("edict_remaining_seconds", 0) or 0) <= 0:
        faith = int(small_world.get("faith", 0) or 0)
        faith_cap = int(small_world.get("faith_cap", 100) or 100)
        if faith_cap > 0 and faith < faith_cap:
            return {"action": "miracle_sermon", "reason": f"信仰 {faith}/{faith_cap}，执行布道"}
        population = int(small_world.get("population", 0) or 0)
        population_cap = int(small_world.get("population_cap", 0) or 0)
        stability = int(small_world.get("stability", 0) or 0)
        stability_cap = int(small_world.get("stability_cap", 100) or 100)
        if (population_cap > 0 and population / population_cap <= 0.95) or (stability_cap > 0 and stability / stability_cap <= 0.80):
            return {"action": "miracle_relief", "reason": "人口或稳定偏低，执行赈灾"}

    if state.get("small_world_harvest_enabled") and small_world.get("can_harvest"):
        return {"action": "collect", "reason": "已显式开启收割香火"}

    if state.get("small_world_refine_enabled"):
        stock = int(small_world.get("incense_stock", 0) or 0)
        amount = max(0, (stock // 10) * 10)
        if amount >= 10:
            return {"action": "refine_shenshi", "payload": {"amount": amount}, "reason": f"淬炼神识 {amount} 香火"}

    return {"reason": "当前无已启用且可执行的小世界动作"}


def _cave_small_world_action_message(result):
    data = dict(result.get("data") or {})
    action_result = data.get("action_result") if isinstance(data.get("action_result"), dict) else {}
    return str(action_result.get("rawMessage") or action_result.get("message") or result.get("error") or "").strip()


def _record_cave_deep_retreat_state(identity_id, action, result, sync_result, *, now, result_msg_id=0):
    data = dict((result or {}).get("data") or {})
    payload = {
        "action": str(action or ""),
        "ok": bool((result or {}).get("ok")),
        "status": str((result or {}).get("status") or ""),
        "sync": dict(sync_result or {}),
    }
    message = extract_cave_deep_seclusion_action_message(data)
    if message:
        payload["message_digest"] = stable_payload_digest(message)
        payload["message_kind"] = (sync_result or {}).get("message_kind", "")
    return record_miniapp_state(
        identity_id,
        "cave_deep_retreat",
        payload,
        source="cave_dwelling_miniapp",
        source_id=f"cave_deep_retreat:{str(action or '')}:{int(result_msg_id or 0)}:{stable_payload_digest(payload)}",
        now=now,
        outputs=("module_snapshot", "deep_retreat_state"),
        replaces_commands=(".深度闭关", ".查看闭关"),
    )


def _capture_store(now):
    day_key = get_day_key(now)
    path = CAVE_TREASURE_MINIAPP_CAPTURE_DIR / f"cave_treasure-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


async def run_cave_public_small_world_sync(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not get_identity_enabled(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    with use_identity(identity_id):
        next_time = float(state.get("next_small_world_time", 0) or 0)
        if next_time > now:
            return {
                "ok": True,
                "message": "洞府小世界尚未到检查时间，已跳过请求",
                "extra": {"skipped": True, "next_time": next_time},
            }
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        with use_identity(identity_id):
            result = await run_cave_small_world_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action_planner=_plan_cave_public_small_world_action,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_small_world:{identity_id}",
            )
            data = dict(result.get("data") or {})
            overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
            small_world = overview.get("small_world") if isinstance(overview.get("small_world"), dict) else {}
            plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
            action = str(data.get("action") or plan.get("action") or "")
            snapshot = _apply_cave_small_world_overview(small_world, now) if small_world else {}
            record = _record_cave_small_world_state(identity_id, result, now=now)
            action_message = _cave_small_world_action_message(result)
            resource_blocked = plan.get("blocked") == "resource" or ("不足" in action_message and action == "manifest")
            if not result.get("ok"):
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                if resource_blocked:
                    state["small_world_last_error"] = f"洞府显灵资源不足：{plan.get('reason') or action_message or '资源不足'}"
                    message = f"洞府小世界显灵资源不足，已退避 6 小时：{plan.get('reason') or action_message or '资源不足'}"
                else:
                    state["small_world_last_error"] = f"洞府小世界处理失败：{result.get('error') or result.get('status') or 'unknown'}"
                    message = f"洞府小世界处理失败，已退避 6 小时：{result.get('error') or result.get('status') or 'unknown'}"
            elif not small_world:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                state["small_world_last_error"] = "洞府小世界回包未包含面板"
                message = "洞府小世界处理完成，但回包未包含面板，已退避 6 小时"
            elif resource_blocked:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + CAVE_SMALL_WORLD_RESOURCE_PAUSE_SEC
                state["small_world_last_error"] = f"洞府显灵资源不足：{plan.get('reason') or '资源不足'}"
                message = f"洞府小世界显灵资源不足，已退避 6 小时：{plan.get('reason') or '资源不足'}"
            elif action:
                state["small_world_refresh_count"] = 0
                state["small_world_phase"] = "idle"
                state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                if action in {"miracle_sermon", "miracle_relief"}:
                    state["small_world_god_cooldown_until"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                state["small_world_last_error"] = ""
                action_label = {
                    "manifest": "显灵",
                    "miracle_sermon": "布道",
                    "miracle_relief": "赈灾",
                    "collect": "收割香火",
                    "refine_shenshi": "神识淬炼",
                }.get(action, action)
                message = f"洞府小世界已{action_label}：{action_message or plan.get('reason') or '处理完成'}"
            else:
                can_refresh = bool(
                    not small_world.get("has_prayer")
                    and state.get("small_world_manifest_enabled")
                    and state.get("small_world_refresh_enabled")
                )
                if can_refresh:
                    refresh_count = int(state.get("small_world_refresh_count", 0) or 0) + 1
                    if refresh_count >= CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS:
                        state["small_world_refresh_count"] = 0
                        state["small_world_phase"] = "idle"
                        state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                        state["small_world_last_error"] = "洞府祈愿刷新 5 次未出现，已退避 6 小时"
                        refresh_note = "刷新 5 次未出现，已退避 6 小时"
                    else:
                        state["small_world_refresh_count"] = refresh_count
                        state["small_world_phase"] = "refresh_wait"
                        state["next_small_world_time"] = now + CAVE_SMALL_WORLD_REFRESH_SEC
                        state["small_world_last_error"] = ""
                        refresh_note = f"10 分钟后刷新 {refresh_count + 1}/{CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS}"
                else:
                    state["small_world_refresh_count"] = 0
                    state["small_world_phase"] = "idle"
                    state["next_small_world_time"] = now + CAVE_SMALL_WORLD_CYCLE_SEC
                    state["small_world_last_error"] = str(plan.get("reason") or "")
                    refresh_note = plan.get("reason") or "无需动作，6 小时后再查"
                faith = small_world.get("faith", 0)
                stability = small_world.get("stability", 0)
                prayer = small_world.get("prayer_title") or "无祈愿"
                message = f"洞府小世界已检查：信仰 {faith}｜稳定 {stability}｜{prayer}｜{refresh_note}"
            save_state()
        await send_audit_log(
            f"🌏 {message}",
            scope="identity",
            send_as_id=identity_id,
            priority="high" if resource_blocked else "low",
            limit=300,
        )
        return {
            "ok": bool(result.get("ok")) or resource_blocked,
            "message": message,
            "extra": {
                "record_key": record.get("record_key", ""),
                "action": action,
                "snapshot": snapshot,
            },
        }


async def run_cave_public_treasure(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not get_identity_enabled(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        result = await run_cave_treasure_miniapp_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            max_steps=CAVE_TREASURE_MANUAL_MAX_STEPS,
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_treasure:{identity_id}",
        )
        inventory_record = _record_cave_treasure_inventory_delta(identity_id, result, now=now)
        state_record = _record_cave_treasure_miniapp_state(identity_id, result, now=now)
        summary = _format_cave_treasure_summary(result)
        message = f"洞府寻宝公共入口：{summary}"
        await send_audit_log(f"🕳️ {message}", scope="identity", send_as_id=identity_id, priority="low" if result.get("ok") else "normal", limit=260)
        return {
            "ok": bool(result.get("ok")),
            "message": message,
            "extra": {
                "inventory_record_key": inventory_record.get("record_key", ""),
                "state_record_key": state_record.get("record_key", ""),
            },
        }


async def run_cave_public_trial(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not get_identity_enabled(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        try:
            dwelling_init_data = await request_cave_treasure_miniapp_init_data(
                identity_id,
                token=token,
                webview_url=webview_url,
            )
        except Exception as exc:
            message = f"洞府天机试炼会话初始化失败：{type(exc).__name__}: {exc}"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
            return {"ok": False, "message": message, "extra": {}}
        cave_result = await run_cave_dwelling_start_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            init_data=dwelling_init_data,
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_trial_start:{identity_id}",
        )
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        overview = cave_data.get("overview") if isinstance(cave_data.get("overview"), dict) else {}
        if not cave_result.get("ok"):
            message = f"洞府天机试炼入口读取失败：{cave_result.get('error') or cave_result.get('status') or 'unknown'}"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}
        player_id = int(overview.get("player_id", 0) or 0)
        if player_id <= 0:
            message = "洞府天机试炼入口读取完成，但回包缺少 playerId"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}
        external_app = _find_trial_external_app_in_cave_payload(raw)
        if not external_app or not external_app.get("available"):
            message = "洞府天机试炼入口读取完成，但外府试炼入口不可用"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}
        launch = {}
        if external_app.get("action"):
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=player_id,
                init_data=dwelling_init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_trial_external:{identity_id}",
            )
            if not external_result.get("ok"):
                message = f"洞府天机试炼动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}"
                await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
                return {"ok": False, "message": message, "extra": {}}
            launch = _find_trial_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _find_trial_launch_in_cave_payload(external_app)
        if not launch:
            message = "洞府天机试炼入口已请求，但未返回可用试炼 URL"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}
        result = await run_trial_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            init_data=dwelling_init_data,
            max_rounds=99,
            capture_sink=_trial_miniapp_capture_store(now),
            capture_source=f"cave_public_trial:{identity_id}",
        )
        summary = _format_trial_summary(result)
        message = f"洞府天机试炼公共入口：{summary}"
        await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="low" if result.get("ok") else "normal", limit=260)
        return {"ok": bool(result.get("ok")), "message": message, "extra": {"trial_title": launch.get("title", "")}}


async def run_cave_public_yuanying(identity_id, public_entry_url, *, now=None):
    """Run the one safe Tianjige command exposed by the public dwelling entry."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not get_identity_enabled(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    with use_identity(identity_id):
        block_reason = yuanying.get_yuanying_block_reason(now)
    block_text = str(block_reason or "").strip()
    if block_text and block_text not in {"无", "-", "none", "None"}:
        return {"ok": False, "message": f"元婴尚未到出窍窗口：{block_text}", "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        result = await run_cave_tianjige_command_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            command=yuanying.CMD_YUANYING,
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_tianjige_yuanying:{identity_id}",
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        sync_result = await sync_cave_tianjige_yuanying_result(identity_id, data, now=now)
        action_ok = bool(result.get("ok")) and _cave_tianjige_action_succeeded(data)
        reply_message = str(sync_result.get("message") or "").strip()
        if not result.get("ok"):
            message = f"洞府天机阁元婴出窍请求失败：{result.get('error') or result.get('status') or 'unknown'}"
        elif not action_ok:
            message = f"洞府天机阁元婴出窍未执行：{reply_message or '游戏未给出可执行结果'}"
        elif not sync_result.get("handled"):
            message = f"洞府天机阁元婴出窍已提交，但回包未能安全同步：{reply_message or '无可识别文案'}"
        else:
            message = f"洞府天机阁元婴出窍：{reply_message}"
        await send_audit_log(
            f"👶 {message}",
            scope="identity",
            send_as_id=identity_id,
            priority="low" if action_ok and sync_result.get("handled") else "normal",
            limit=320,
        )
        return {
            "ok": bool(action_ok and sync_result.get("handled")),
            "message": message,
            "extra": {"sync": sync_result},
        }


async def run_cave_public_deep_retreat_action(identity_id, public_entry_url, action, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    action = str(action or "").strip()
    if action not in {"status", "start", "settle", "force"}:
        return {"ok": False, "message": "洞府闭关动作仅允许 status/start/settle/force", "extra": {}}
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not get_identity_enabled(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        result = await run_cave_deep_seclusion_action_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            action=action,
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_deep_retreat:{identity_id}:{action}",
        )
        sync_result = await sync_cave_deep_seclusion_action_result(identity_id, action, result.get("data") or {}, now=now)
        record = _record_cave_deep_retreat_state(identity_id, action, result, sync_result, now=now)
        if not result.get("ok"):
            message = f"洞府闭关 {action} 失败：{result.get('error') or result.get('status') or 'unknown'}"
        else:
            phase = (sync_result or {}).get("phase") or "-"
            handled = "已同步" if (sync_result or {}).get("handled") else "未改状态"
            message = f"洞府闭关 {action} 完成：{handled}｜阶段 {phase}"
        await send_audit_log(f"🧘 {message}", scope="identity", send_as_id=identity_id, priority="low", limit=240)
        return {"ok": bool(result.get("ok")), "message": message, "extra": {"record_key": record.get("record_key", ""), "sync": sync_result}}


async def handle_cave_treasure_miniapp_entry(event, text, now, reply_to=None, matched_family=None, result_msg_id=0, require_identity_match=False):
    identity_id = _identity_id()
    if identity_id <= 0 or not _has_manual_auth(identity_id, now):
        return False
    if require_identity_match and not _entry_mentions_current_identity(text):
        return False
    launch = extract_cave_treasure_miniapp_launch(event, message_text=text)
    if not launch:
        return False
    global_enabled = get_global_enabled()
    maintenance_miniapp_allowed = _miniapp_http_allowed_during_pause()
    identity_enabled = get_identity_enabled(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not identity_enabled:
        revoke_cave_treasure_miniapp_manual_run(identity_id)
        reason = "全局暂停" if not global_enabled else "身份已停用"
        await send_audit_log(f"🕳️ 洞府寻宝 MiniApp {reason}，已跳过 WebView/HTTP 接管。", scope="identity", limit=180)
        return True

    lock = _run_lock(identity_id)
    if lock.locked():
        await send_audit_log("🕳️ 洞府寻宝 MiniApp 已在执行，重复入口忽略。", scope="identity", limit=160)
        return True

    async with lock:
        revoke_cave_treasure_miniapp_manual_run(identity_id)
        await send_audit_log(
            "🕳️ 洞府寻宝 MiniApp 接管入口，开始 WebView/HTTP 流程。"
            + ("（天尊维护暂停中，仅执行 MiniApp HTTP）" if maintenance_miniapp_allowed else ""),
            scope="identity",
            priority="low",
            limit=180,
        )
        result = await run_cave_treasure_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            max_steps=CAVE_TREASURE_MANUAL_MAX_STEPS,
            capture_sink=_capture_store(now),
            capture_source=f"cave_treasure_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}",
        )
        summary = _format_cave_treasure_summary(result)
        _record_cave_treasure_inventory_delta(
            identity_id,
            result,
            now=now,
            result_msg_id=int(result_msg_id or getattr(event, "id", 0) or 0),
        )
        _record_cave_treasure_miniapp_state(
            identity_id,
            result,
            now=now,
            result_msg_id=int(result_msg_id or getattr(event, "id", 0) or 0),
        )
        priority = "low" if dict(result or {}).get("ok") else "normal"
        await send_audit_log(f"🕳️ 洞府寻宝结果｜{summary}", scope="identity", priority=priority, limit=260)
        return True


__all__ = [
    "CAVE_TREASURE_MANUAL_AUTH_TTL_SEC",
    "CAVE_TREASURE_MANUAL_MAX_STEPS",
    "authorize_cave_treasure_miniapp_manual_run",
    "extract_cave_deep_seclusion_action_message",
    "extract_cave_tianjige_command_message",
    "handle_cave_treasure_miniapp_entry",
    "revoke_cave_treasure_miniapp_manual_run",
    "run_cave_public_deep_retreat_action",
    "run_cave_public_small_world_sync",
    "run_cave_public_treasure",
    "run_cave_public_trial",
    "run_cave_public_yuanying",
    "sync_cave_deep_seclusion_action_result",
    "sync_cave_tianjige_yuanying_result",
    "_find_trial_launch_in_cave_payload",
    "_cave_treasure_inventory_items",
    "_record_cave_deep_retreat_state",
    "_record_cave_small_world_state",
    "_record_cave_treasure_miniapp_state",
]
