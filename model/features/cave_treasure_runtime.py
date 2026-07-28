import asyncio
import re
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin

from ..config import CD_BUFFER_SEC, CMD_TIANTI_STATUS, STATE_DIR
from ..inventory_delta import record_inventory_delta, stable_payload_digest
from ..miniapp_state import record_miniapp_state
from ..persistence import save_state
from ..runtime import console_log, send_audit_log
from ..state import get_current_identity_id, get_global_enabled, get_global_pause_source, get_identity_account, get_identity_enabled, get_miniapp_state_records, get_send_as_profile, is_cave_public_identity_available, state, use_identity
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from . import concubine, deep_retreat, fishing_behavior, stargazer, tianti, tree_runtime, yinluo, yuanying
from .small_world import SMALL_WORLD_PREACH_FAITH_RATIO_TRIGGER
from .cave_treasure_miniapp import (
    CAVE_TIANJIGE_READ_ONLY_COMMANDS,
    build_cave_treasure_launch_args,
    extract_cave_treasure_miniapp_launch,
    find_cave_external_app,
    merge_cave_dwelling_snapshot_data,
    parse_cave_dwelling_overview,
    request_cave_treasure_miniapp_init_data,
    run_cave_deep_seclusion_action_production_flow,
    run_cave_dwelling_start_production_flow,
    run_cave_dwelling_snapshot_production_flow,
    run_cave_external_action_production_flow,
    run_cave_journey_action_production_flow,
    run_cave_small_world_production_flow,
    run_cave_tianjige_command_production_flow,
    run_cave_treasure_miniapp_production_flow,
)
from .trial_miniapp import build_trial_launch_args
from .trial_runtime import _format_trial_summary, _record_trial_business_capture, _trial_batch_materials, _trial_miniapp_capture_store, run_trial_miniapp_production_flow
from .stargazer_miniapp import build_stargazer_launch_args, run_stargazer_miniapp_production_flow
from .tree_miniapp import build_tree_launch_args
from .fishing_miniapp import extract_fishing_miniapp_launch_from_dwelling_payload, run_fishing_miniapp_production_flow
from .tower_miniapp import build_tower_launch_args, format_tower_delta, run_tower_miniapp_production_flow
from .miniapp_common import append_business_capture, resolve_identity_id as _identity_id
from .fishing_runtime import (
    _apply_fishing_miniapp_result,
    _fishing_miniapp_capture_store,
    _fishing_reset_jitter_sec,
    _record_fishing_business_capture,
    _remaining_miniapp_chain_rounds,
    _send_fishing_daily_completion_summary,
)


CAVE_TREASURE_MANUAL_AUTH_TTL_SEC = 10 * 60
CAVE_TREASURE_MANUAL_MAX_STEPS = 48
CAVE_TREASURE_MINIAPP_CAPTURE_DIR = Path(STATE_DIR) / "miniapp_capture"
CAVE_SMALL_WORLD_RESOURCE_PAUSE_SEC = 6 * 3600
CAVE_SMALL_WORLD_CYCLE_SEC = 6 * 3600
CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC = 8 * 3600
CAVE_SMALL_WORLD_HARVEST_RETRY_SEC = 30 * 60
CAVE_SMALL_WORLD_GOD_COOLDOWN_SEC = 3 * 3600
CAVE_SMALL_WORLD_REFRESH_SEC = 10 * 60
CAVE_SMALL_WORLD_MAX_REFRESH_ATTEMPTS = 5
CAVE_SMALL_WORLD_MIN_REQUEST_SEC = 10 * 60
CAVE_DEEP_STATUS_RECHECK_SEC = 30 * 60
CAVE_YUANYING_STATUS_RECHECK_SEC = 30 * 60
WILD_TRAINING_NO_COOLDOWN_FOLLOWUP_SEC = 60

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


_ITEM_TEXT_RE = re.compile(
    r"(?:获得|奖励|收获|掉落|战利品|材料)?\s*(?:【(?P<bracket>[^】]+)】|(?P<plain>[\u4e00-\u9fffA-Za-z0-9_·-]{2,24}))\s*[xX×]\s*(?P<count>[\d,]+)"
)
_GAIN_TEXT_RE = re.compile(r"(?P<name>修为|经验|灵石|天机残痕)\s*[+＋]\s*(?P<count>[\d,]+)")
_MENTION_RE = re.compile(r"@([A-Za-z0-9_]{3,64})")
_INVENTORY_GAIN_NAMES = {"灵石"}
_CAVE_TREASURE_STATE_OUTPUTS = ("module_snapshot", "daily_counter", "inventory_delta")




def _miniapp_http_allowed_during_pause():
    """天尊维护暂停期间仍允许 MiniApp HTTP。

    刻意保留在各模块本地而不是收进 miniapp_common：测试普遍用
    patch.object(<该模块>, "get_global_enabled") 打桩，判断一旦搬走，
    62 处 patch 点就都失效了。这点重复换来的是打桩位置符合直觉。
    """
    return (not get_global_enabled()) and get_global_pause_source() == "tianzun_maintenance"


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


def is_cave_public_entry_busy(identity_id):
    """Return whether this identity currently owns an in-process public-entry operation."""
    lock = _PUBLIC_ENTRY_LOCKS.get(_identity_id(identity_id))
    return bool(lock and lock.locked())


def _public_entry_allowed():
    return get_global_enabled() or get_global_pause_source() == "tianzun_maintenance"


def _parse_public_cave_entry_url(public_entry_url):
    launch, _args = build_cave_treasure_launch_args(str(public_entry_url or "").strip())
    if not launch.allowed or not launch.start_param:
        return "", "", launch.reason or "invalid cave public entry"
    return launch.start_param, launch.webview_url, ""


def _channel_identity_treasure_allowed(identity_id):
    """Lab allowlist: channel identities permitted to run treasure themselves.

    2026-07-27 live probe settled this: the selected panel does return a
    dwelling.hunt block per playerId, but its *values* are the login account's
    shared quota. xuruode8 (channel identity of 301299112, which had already
    used 3/3 that day) got HTTP 409 daily_limit on its very first hunt without
    ever running treasure itself. The account-shared gate below is therefore
    correct; this allowlist (default empty) exists only for re-testing if the
    game ever changes that behavior.
    """
    try:
        from ..ui import normalize_miniapp_auto_config

        allowed = normalize_miniapp_auto_config().get("cave_public_treasure_channel_identity_ids") or ()
        return int(identity_id or 0) in {
            int(value) for value in allowed if str(value or "").strip().lstrip("-").isdigit()
        }
    except Exception:
        return False


def _public_entry_account_identity_error(identity_id):
    """Treasure attempts are shared by the physical Telegram login account."""
    identity_id = _identity_id(identity_id)
    try:
        account_id = int(get_identity_account(identity_id) or 0)
    except (TypeError, ValueError, OverflowError):
        account_id = 0
    if account_id > 0 and account_id != identity_id:
        if _channel_identity_treasure_allowed(identity_id):
            return ""
        return "洞府寻宝次数按登录账号共享，请使用该账号本体身份执行"
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
    item = find_cave_external_app(
        value,
        keys=("trial", "tianji_trial"),
        actions=("trial", "tianji_trial"),
        title_terms=("天机试炼",),
        url_terms=("xianxia-trial",),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
    normalized_action = action if action in {"trial", "tianji_trial"} else ""
    if not normalized_action and key == "tianji_trial" and url in {"", "#"}:
        normalized_action = "tianji_trial"
    return {
        "action": normalized_action,
        "url": url,
        "title": str(item.get("title") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
    }


def _find_fishing_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("fishing", "fish"),
        actions=("fishing",),
        title_terms=("钓",),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
    return {
        "action": "fishing" if action == "fishing" or url in {"", "#"} else "",
        "url": url,
        "title": str(item.get("title") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
    }


def _find_stargazer_external_app_in_cave_payload(value):
    root = value.get("data") if isinstance(value, dict) and isinstance(value.get("data"), dict) else value
    account = root.get("account") if isinstance(root, dict) and isinstance(root.get("account"), dict) else {}
    star_palace = account.get("starPalace") if isinstance(account.get("starPalace"), dict) else {}
    observatory = star_palace.get("observatory") if isinstance(star_palace.get("observatory"), dict) else {}
    observatory_url = str(
        observatory.get("url") or observatory.get("webviewUrl") or observatory.get("webview_url") or ""
    ).strip()
    observatory_action = str(observatory.get("action") or "").strip().lower()
    if observatory_url or observatory_action:
        return {
            "action": observatory_action,
            "url": observatory_url,
            "title": str(observatory.get("title") or "观星台").strip(),
            "available": bool(observatory.get("available", True)),
            "key": "stargazer",
        }
    item = find_cave_external_app(
        value,
        keys=("sect_farm", "stargazer", "star_palace", "star_farm"),
        actions=("sect_farm", "stargazer", "star_palace", "star_farm"),
        title_terms=("观星台", "星宫"),
        url_terms=("xianxia-sect-farm", "startapp=farm_"),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    return {
        "action": str(item.get("action") or "").strip().lower(),
        "url": str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip(),
        "title": str(item.get("title") or item.get("subtitle") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
        "key": key,
    }


def _find_tree_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("spirit_tree", "tree", "luoyun_tree"),
        actions=("spirit_tree", "tree", "luoyun_tree"),
        title_terms=("灵树",),
        url_terms=("xianxia-spirit-tree", "startapp=tree_"),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    return {
        "action": str(item.get("action") or "").strip().lower(),
        "url": str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip(),
        "title": str(item.get("title") or item.get("subtitle") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
        "key": key,
    }


def _find_tower_external_app_in_cave_payload(value):
    item = find_cave_external_app(
        value,
        keys=("pagoda", "tower", "liuli_pagoda"),
        actions=("pagoda",),
        title_terms=("问心塔", "琉璃塔"),
        url_terms=("xianxia-pagoda", "startapp=pagoda_"),
    )
    if not item:
        return {}
    key = str(item.get("key") or "").strip().lower()
    action = str(item.get("action") or "").strip().lower()
    url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
    return {
        "action": "pagoda" if action == "pagoda" or url in {"", "#"} else "",
        "url": url,
        "title": str(item.get("title") or item.get("subtitle") or item.get("buttonText") or key).strip(),
        "available": bool(item.get("available", True)),
        "key": key,
    }


def _tree_launch_from_external_app(external_app):
    url = str((external_app or {}).get("url") or "").strip()
    if not url:
        return {}
    if url.startswith("/"):
        url = urljoin("https://asc.aiopenai.app/", url)
    elif "://" not in url:
        url = urljoin("https://asc.aiopenai.app/miniapp/xianxia-dwelling", url)
    launch, _args = build_tree_launch_args(url)
    if not launch.allowed or not launch.start_param:
        return {}
    return {
        "token": launch.start_param,
        "webview_url": launch.webview_url,
        "title": str((external_app or {}).get("title") or "").strip(),
        "safe_summary": launch.safe_summary(),
    }


def _find_tree_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        launch = _tree_launch_from_external_app(item)
        if launch:
            return launch
    return {}


def _stargazer_launch_from_external_app(external_app):
    url = str((external_app or {}).get("url") or "").strip()
    if not url:
        return {}
    if url.startswith("/"):
        url = urljoin("https://asc.aiopenai.app/", url)
    launch, _args = build_stargazer_launch_args(url)
    if not launch.allowed or not launch.start_param:
        return {}
    return {
        "token": launch.start_param,
        "webview_url": launch.webview_url,
        "title": str((external_app or {}).get("title") or "").strip(),
        "safe_summary": launch.safe_summary(),
    }


def _find_stargazer_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        launch = _stargazer_launch_from_external_app(item)
        if launch:
            return launch
    return {}


def _find_tower_launch_in_cave_payload(value):
    for item in _iter_dicts(value):
        url = str(item.get("url") or item.get("webviewUrl") or item.get("webview_url") or "").strip()
        if not url:
            continue
        if url.startswith("/"):
            url = urljoin("https://asc.aiopenai.app/", url)
        elif "://" not in url:
            url = urljoin("https://asc.aiopenai.app/miniapp/xianxia-dwelling", url)
        launch, _args = build_tower_launch_args(url)
        if launch.allowed and launch.start_param:
            return {
                "token": launch.start_param,
                "webview_url": launch.webview_url,
                "title": str(item.get("title") or item.get("buttonText") or item.get("key") or "").strip(),
                "safe_summary": launch.safe_summary(),
            }
    return {}


def _selected_player_error(overview, identity_id):
    selected_player_id = _parse_int((overview or {}).get("player_id"), 0)
    if not selected_player_id:
        return "洞府回包缺少 playerId"
    if _normalize_dwelling_identity_id(selected_player_id) != _normalize_dwelling_identity_id(identity_id):
        return f"洞府身份校验失败：期望 {int(identity_id or 0)}，实际 {selected_player_id}"
    return ""


def _normalize_dwelling_identity_id(player_id):
    player_id = _parse_int(player_id, 0)
    if player_id <= -1_000_000_000_000:
        return -player_id - 1_000_000_000_000
    return player_id


def _resolve_dwelling_player_id(payload, identity_id):
    root = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    identity = root.get("identity") if isinstance(root, dict) and isinstance(root.get("identity"), dict) else {}
    target_identity_id = _normalize_dwelling_identity_id(identity_id)
    for choice in identity.get("choices") or ():
        if not isinstance(choice, dict):
            continue
        player_id = _parse_int(choice.get("playerId"), 0)
        if player_id and _normalize_dwelling_identity_id(player_id) == target_identity_id:
            return player_id
    return 0


def _has_cave_details_snapshot(payload):
    root = payload.get("data") if isinstance(payload, dict) and isinstance(payload.get("data"), dict) else payload
    account = root.get("account") if isinstance(root, dict) and isinstance(root.get("account"), dict) else {}
    if account.get("deferredPending") is False:
        return True
    external = account.get("externalApps") if isinstance(account.get("externalApps"), dict) else {}
    if isinstance(external.get("groups"), list) and external.get("groups"):
        return True
    if isinstance(account.get("journey"), dict) and account.get("journey"):
        return True
    if isinstance(account.get("smallWorld"), dict) and account.get("smallWorld"):
        return True
    if isinstance(account.get("starPalace"), dict) and account.get("starPalace"):
        return True
    command_center = account.get("commandCenter") if isinstance(account.get("commandCenter"), dict) else {}
    return isinstance(command_center.get("entries"), list) and bool(command_center.get("entries"))


def _cave_entry_safe_directory(result):
    """Build a stable, secret-free catalog from an existing dwelling response."""
    data = (result or {}).get("data") if isinstance((result or {}).get("data"), dict) else {}
    overview = data.get("overview") if isinstance(data.get("overview"), dict) else {}
    if not overview:
        overview = parse_cave_dwelling_overview(data.get("raw") or {})

    external_apps = []
    seen_apps = set()
    for app in overview.get("external_apps") or ():
        if not isinstance(app, dict):
            continue
        item = {
            "key": str(app.get("key") or "").strip(),
            "title": str(app.get("title") or "").strip(),
            "action": str(app.get("action") or "").strip(),
            "start_kind": str(app.get("start_kind") or "").strip(),
            "group": str(app.get("group_key") or app.get("group_title") or "").strip(),
        }
        signature = tuple(item.values())
        if not any(signature) or signature in seen_apps:
            continue
        seen_apps.add(signature)
        external_apps.append(item)
    external_apps.sort(key=lambda item: (
        item.get("group") or "",
        item.get("key") or "",
        item.get("title") or "",
        item.get("action") or "",
        item.get("start_kind") or "",
    ))

    command_center = overview.get("command_center") if isinstance(overview.get("command_center"), dict) else {}
    center_entries = []
    seen_entries = set()
    for entry in command_center.get("entries") or ():
        if not isinstance(entry, dict):
            continue
        commands = tuple(
            str(command or "").strip()
            for command in entry.get("commands") or ()
            if str(command or "").strip()
        )
        item = {
            "key": str(entry.get("key") or "").strip(),
            "title": str(entry.get("title") or "").strip(),
            "status": str(entry.get("status") or "").strip(),
            "target_tab": str(entry.get("target_tab") or "").strip(),
            "button_text": str(entry.get("button_text") or "").strip(),
            "note": str(entry.get("note") or "").strip(),
            "commands": list(commands),
        }
        signature = (
            item["key"],
            item["title"],
            item["status"],
            item["target_tab"],
            item["button_text"],
            item["note"],
            commands,
        )
        if not any(signature[:-1]) and not commands:
            continue
        if signature in seen_entries:
            continue
        seen_entries.add(signature)
        center_entries.append(item)
    center_entries.sort(key=lambda item: (
        item.get("target_tab") or "",
        item.get("key") or "",
        item.get("title") or "",
    ))

    directory = {}
    if external_apps:
        directory["external_apps"] = external_apps
    if center_entries:
        security = command_center.get("security") if isinstance(command_center.get("security"), dict) else {}
        directory["command_center"] = {
            "entry_count": len(center_entries),
            "security": {
                "mode": str(security.get("mode") or "").strip(),
                "direct_raw_command": bool(security.get("direct_raw_command")),
                "max_input_length": _parse_int(security.get("max_input_length"), 0),
                "text": str(security.get("text") or "").strip(),
            },
            "entries": center_entries,
        }
    return directory


def _record_cave_entry_safe_directory(identity_id, result, *, now):
    directory = _cave_entry_safe_directory(result)
    if not directory:
        return {"changed": False, "record": {}, "record_key": ""}
    record_key = f"{int(identity_id)}:cave_entry_directory"
    previous = dict(get_miniapp_state_records().get(record_key) or {})
    if previous.get("state") == directory:
        return {"changed": False, "record": previous, "record_key": record_key}
    return record_miniapp_state(
        identity_id,
        "cave_entry_directory",
        directory,
        source="cave_dwelling_miniapp",
        source_id="cave_entry_directory:v1",
        now=now,
        outputs=("module_catalog",),
    )


async def _load_cave_public_identity_session(
    identity_id,
    token,
    webview_url,
    *,
    now,
    capture_source,
    include_details=False,
):
    try:
        init_data = await request_cave_treasure_miniapp_init_data(
            identity_id,
            token=token,
            webview_url=webview_url,
        )
    except Exception as exc:
        return {"ok": False, "error": f"会话初始化失败：{type(exc).__name__}: {exc}"}

    initial_result = await run_cave_dwelling_start_production_flow(
        identity_id,
        token=token,
        webview_url=webview_url,
        init_data=init_data,
        capture_sink=_capture_store(now),
        capture_source=f"{capture_source}:initial",
    )
    if not initial_result.get("ok"):
        return {
            "ok": False,
            "error": initial_result.get("error") or initial_result.get("status") or "initial_start_failed",
        }
    initial_data = dict(initial_result.get("data") or {})
    initial_overview = initial_data.get("overview") if isinstance(initial_data.get("overview"), dict) else {}
    initial_player_id = _parse_int(initial_overview.get("player_id"), 0)
    if initial_player_id and _normalize_dwelling_identity_id(initial_player_id) == _normalize_dwelling_identity_id(identity_id):
        session = {
            "ok": True,
            "init_data": init_data,
            "player_id": initial_player_id,
            "result": initial_result,
        }
    else:
        selected_player_id = _resolve_dwelling_player_id(initial_data.get("raw") or {}, identity_id)
        if not selected_player_id:
            return {"ok": False, "error": "洞府公共入口不包含目标身份"}
        selected_result = await run_cave_dwelling_start_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            init_data=init_data,
            player_id=selected_player_id,
            capture_sink=_capture_store(now),
            capture_source=f"{capture_source}:selected",
        )
        if not selected_result.get("ok"):
            return {
                "ok": False,
                "error": selected_result.get("error") or selected_result.get("status") or "selected_start_failed",
            }
        selected_data = dict(selected_result.get("data") or {})
        player_error = _selected_player_error(selected_data.get("overview") or {}, identity_id)
        if player_error:
            return {"ok": False, "error": player_error}
        session = {
            "ok": True,
            "init_data": init_data,
            "player_id": selected_player_id,
            "result": selected_result,
        }

    session_raw = dict((session.get("result") or {}).get("data") or {}).get("raw") or {}
    if not include_details or _has_cave_details_snapshot(session_raw):
        _record_cave_entry_safe_directory(identity_id, session.get("result") or {}, now=now)
        return session
    details_result = await run_cave_dwelling_snapshot_production_flow(
        identity_id,
        token=token,
        webview_url=webview_url,
        endpoint="details",
        init_data=init_data,
        player_id=session.get("player_id"),
        capture_sink=_capture_store(now),
        capture_source=f"{capture_source}:details",
    )
    if not details_result.get("ok"):
        return {
            "ok": False,
            "error": details_result.get("error") or details_result.get("status") or "details_failed",
        }
    start_data = dict((session.get("result") or {}).get("data") or {})
    merged_raw = merge_cave_dwelling_snapshot_data(
        start_data.get("raw") or {},
        details_result.get("data") or {},
    )
    session["result"] = {
        **dict(session.get("result") or {}),
        "data": {
            **start_data,
            "overview": parse_cave_dwelling_overview(merged_raw),
            "raw": merged_raw,
        },
    }
    _record_cave_entry_safe_directory(identity_id, session.get("result") or {}, now=now)
    return session


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
        if status == "daily_limit" and not material_text:
            return f"MiniApp {status}{games}｜今日次数已尽"
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


def extract_cave_deep_seclusion_state(data):
    """Extract authoritative deep-seclusion fields from action or dwelling payloads."""

    data = data if isinstance(data, dict) else {}
    action_result = data.get("actionResult") if isinstance(data.get("actionResult"), dict) else {}
    deep_state = {}
    for item in _iter_nested_dicts(data):
        normalized_keys = {_normalize_key(key) for key in item}
        if "remainingseconds" not in normalized_keys:
            continue
        if normalized_keys.intersection({"active", "cansettle", "canstart", "statuscommand", "statustext"}):
            deep_state = item
            break

    def optional_bool(container, *keys):
        for key in keys:
            if key in container and isinstance(container.get(key), bool):
                return container.get(key)
        return None

    remaining_raw = action_result.get(
        "remainingSeconds",
        action_result.get(
            "remaining_seconds",
            deep_state.get("remainingSeconds", deep_state.get("remaining_seconds")),
        ),
    )
    remaining_seconds = None if remaining_raw is None else max(0, _parse_int(remaining_raw, 0))
    return {
        "known": bool(action_result or deep_state),
        "ok": optional_bool(action_result, "ok"),
        "completed": optional_bool(action_result, "completed"),
        "active": optional_bool(deep_state, "active"),
        "can_settle": optional_bool(deep_state, "canSettle", "can_settle"),
        "remaining_seconds": remaining_seconds,
        "message": extract_cave_deep_seclusion_action_message(data),
    }


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
    return action_result.get("ok") is True


async def sync_cave_tianjige_yuanying_result(identity_id, data, *, now, command=None):
    """Replay only safe Tianjige YuanYing outcomes into the existing state machine.

    The normal status handler can emit a legacy group command for `窍中温养`.
    A public-entry response must never trigger that side effect, so this bridge
    handles success and explicit cooldown wording only.
    """
    identity_id = _identity_id(identity_id)
    message = extract_cave_tianjige_command_message(data)
    if identity_id <= 0 or not message:
        return {"handled": False, "reason": "missing_identity_or_message", "message": "", "phase": ""}

    command = str(command or yuanying.CMD_YUANYING).strip()
    with use_identity(identity_id):
        if not _cave_tianjige_action_succeeded(data):
            return {
                "handled": False,
                "ready": False,
                "reason": "action_rejected",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        plain_message = re.sub(r"[*_`]+", "", message)
        status_ready = bool(
            command == yuanying.CMD_YUANYING_STATUS
            and re.search(r"状态\s*[:：]\s*窍中温养", plain_message)
            and not any(token in plain_message for token in ("不可", "不能", "暂不", "尚未", "冷却", "等待", "休息", "不足"))
        )
        if status_ready:
            state["yuanying_probe_pending"] = False
            yuanying.clear_yuanying_summary_flags()
            yuanying.set_yuanying_phase("idle")
            state["next_yuanying_time"] = float(now)
            save_state()
            return {
                "handled": True,
                "ready": True,
                "reason": "",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        if command == yuanying.CMD_YUANYING_STATUS and re.search(r"状态\s*[:：]\s*元婴闭关", plain_message):
            state["yuanying_probe_pending"] = False
            yuanying.clear_yuanying_summary_flags()
            yuanying.set_yuanying_phase("running")
            state["next_yuanying_time"] = float(now) + CAVE_YUANYING_STATUS_RECHECK_SEC
            save_state()
            return {
                "handled": True,
                "ready": False,
                "reason": "active_yuanying_retreat",
                "message": message,
                "phase": str(state.get("yuanying_phase") or ""),
            }

        reply_to = SimpleNamespace(raw_text=command, id=0)
        handled = await yuanying.handle_yuanying_success_reply(
            message,
            now,
            reply_to=reply_to,
            matched_family="yuanying",
        )
        if handled:
            return {
                "handled": True,
                "ready": False,
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
            "ready": False,
            "reason": "" if handled else "unrecognized_or_nonterminal_message",
            "message": message,
            "phase": str(state.get("yuanying_phase") or ""),
        }


async def sync_cave_deep_seclusion_action_result(identity_id, action, data, *, now):
    """Replay a dwelling MiniApp deep-seclusion result through deep-retreat handlers."""

    identity_id = _identity_id(identity_id)
    action = str(action or "").strip()
    snapshot = extract_cave_deep_seclusion_state(data)
    message = extract_cave_deep_seclusion_action_message(data)
    if action == "status" and not message:
        message = _extract_cave_deep_seclusion_status_message(data)
    if identity_id <= 0 or (not message and not snapshot.get("known")):
        return {"handled": False, "reason": "missing_identity_or_message", "message_kind": ""}

    with use_identity(identity_id):
        if action == "settle":
            remaining_seconds = snapshot.get("remaining_seconds")
            still_running = (
                (remaining_seconds is not None and remaining_seconds > 0)
                or snapshot.get("completed") is False
                or (snapshot.get("active") is True and snapshot.get("can_settle") is not True)
            )
            if still_running:
                wait_sec = remaining_seconds if remaining_seconds and remaining_seconds > 0 else CAVE_DEEP_STATUS_RECHECK_SEC
                deep_retreat.mark_deep_retreat_success(now, now + wait_sec + deep_retreat.CD_BUFFER_SEC)
                return {
                    "handled": True,
                    "ready": False,
                    "reason": "still_running",
                    "message_kind": "running",
                    "phase": str(state.get("deep_retreat_phase") or ""),
                    "remaining_seconds": remaining_seconds,
                }
            if "深度闭关总结" not in message and "功成圆满" not in message:
                deep_retreat.clear_deep_retreat_summary_flags()
                deep_retreat.set_deep_retreat_phase("launching")
                state["deep_retreat_probe_pending"] = False
                state["next_deep_retreat_time"] = now + CAVE_DEEP_STATUS_RECHECK_SEC
                save_state()
                return {
                    "handled": False,
                    "reason": "ambiguous_settle_recheck_status",
                    "message_kind": "other",
                    "phase": "launching",
                }
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
    state = dict(data.get("state") or {}) if isinstance(data.get("state"), dict) else {}
    if not state:
        return {"changed": False, "record": {}, "record_key": ""}
    if str((result or {}).get("status") or "").strip() == "result_unknown":
        state["outcome_unknown"] = True
        state["outcome_unknown_day"] = get_day_key(now)
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


def _cave_treasure_unknown_hold(identity_id, now):
    record = dict(get_miniapp_state_records().get(f"{int(identity_id)}:cave_treasure") or {})
    record_state = record.get("state") if isinstance(record.get("state"), dict) else {}
    return bool(
        record_state.get("outcome_unknown")
        and str(record_state.get("outcome_unknown_day") or "") == get_day_key(now)
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


def _cave_small_world_silence_threshold():
    if not state.get("small_world_high_stock_silence_enabled", False):
        return 0
    try:
        configured = int(state.get("small_world_barrier_min_stock", 130000) or 130000)
    except (TypeError, ValueError):
        configured = 130000
    return max(100_000, configured)


def _cave_small_world_high_stock_silence(small_world):
    threshold = _cave_small_world_silence_threshold()
    try:
        stock = int((small_world or {}).get("incense_stock", 0) or 0)
    except (TypeError, ValueError):
        stock = 0
    if threshold <= 0 or stock < threshold:
        return None
    return {
        "silent": True,
        "suppress_refresh": True,
        "reason": f"高香火静默：库存 {stock} 已达阈值 {threshold}，跳过刷新/维护",
    }


def _apply_cave_small_world_overview(small_world, now):
    snapshot = _cave_small_world_panel_snapshot(small_world, now)
    state["small_world_last_panel_at"] = float(now)
    state["small_world_faith_value"] = int(snapshot.get("faith", 0) or 0)
    state["small_world_pending_incense"] = float(snapshot.get("pending_incense", 0) or 0)
    state["small_world_incense_stock"] = int(snapshot.get("stock", 0) or 0)
    state["small_world_panel_snapshot"] = snapshot
    return snapshot


def _cave_small_world_harvest_due(now):
    if not state.get("small_world_harvest_enabled"):
        return False
    return float(state.get("small_world_next_public_harvest_at", 0) or 0) <= float(now or time.time())


def _cave_small_world_prayer_due_at(small_world, now):
    small_world = small_world if isinstance(small_world, dict) else {}
    if small_world.get("has_prayer"):
        return float(now)
    try:
        remaining = int(small_world.get("prayer_remaining_seconds", 0) or 0)
    except (TypeError, ValueError):
        remaining = 0
    if remaining <= 0:
        return 0.0
    return float(now + remaining + CD_BUFFER_SEC)


def _cave_small_world_next_check_at(small_world, now, *, default_delay=CAVE_SMALL_WORLD_CYCLE_SEC):
    prayer_due_at = _cave_small_world_prayer_due_at(small_world, now)
    if prayer_due_at > 0:
        return prayer_due_at
    return float(now + default_delay)


def _preserve_small_world_timer_after_harvest(existing_next_time, small_world, now):
    """A harvest-only MiniApp pass must not postpone the prayer state machine."""
    try:
        existing_next_time = float(existing_next_time or 0)
    except (TypeError, ValueError):
        existing_next_time = 0.0
    if existing_next_time > 0:
        return existing_next_time
    return _cave_small_world_prayer_due_at(small_world, now)


def _plan_cave_public_small_world_action(overview, *, now=None):
    now = float(now or time.time())
    small_world = overview.get("small_world") if isinstance(overview, dict) and isinstance(overview.get("small_world"), dict) else {}
    if not small_world or not small_world.get("available") or not small_world.get("has_world"):
        return {"reason": "小世界尚不可用"}

    harvest_due = _cave_small_world_harvest_due(now)
    can_harvest = bool(small_world.get("can_harvest"))
    harvest_checked = bool(harvest_due and not can_harvest)

    if small_world.get("has_prayer"):
        if state.get("small_world_manifest_enabled") and small_world.get("can_manifest") and small_world.get("prayer_resources_ready"):
            return {"action": "manifest", "reason": f"处理祈愿 {small_world.get('prayer_title') or '凡人祈愿'}"}
        if harvest_due and can_harvest:
            return {"action": "collect", "harvest_due": True, "reason": "8 小时收割到期，祈愿暂不可处理"}
        if not state.get("small_world_manifest_enabled"):
            return {"harvest_due": harvest_due, "harvest_checked": harvest_checked, "reason": "检测到祈愿，但自动显灵未开启"}
        missing = small_world.get("prayer_missing_resources") or []
        missing_text = "、".join(
            f"{item.get('name') or '资源'}缺{int(item.get('missing', 0) or 0)}"
            for item in missing
            if isinstance(item, dict)
        )
        return {
            "blocked": "resource",
            "harvest_due": harvest_due,
            "harvest_checked": harvest_checked,
            "reason": missing_text or "显灵资源不足或当前不可显灵",
        }

    silence_plan = _cave_small_world_high_stock_silence(small_world)
    if silence_plan:
        silence_plan.update({"harvest_due": harvest_due, "harvest_checked": harvest_due})
        return silence_plan

    if harvest_due and can_harvest:
        return {"action": "collect", "harvest_due": True, "reason": "MiniApp 8 小时收割到期"}

    if state.get("small_world_preach_enabled") and int(small_world.get("edict_remaining_seconds", 0) or 0) <= 0:
        faith = int(small_world.get("faith", 0) or 0)
        faith_cap = int(small_world.get("faith_cap", 100) or 100)
        if faith > 0 and faith_cap > 0 and faith / faith_cap <= SMALL_WORLD_PREACH_FAITH_RATIO_TRIGGER:
            return {
                "action": "miracle_sermon",
                "harvest_due": harvest_due,
                "harvest_checked": harvest_checked,
                "reason": f"信仰 {faith}/{faith_cap}，执行布道",
            }

    if state.get("small_world_refine_enabled"):
        stock = int(small_world.get("incense_stock", 0) or 0)
        amount = max(0, (stock // 10) * 10)
        if amount >= 10:
            return {
                "action": "refine_shenshi",
                "payload": {"amount": amount},
                "harvest_due": harvest_due,
                "harvest_checked": harvest_checked,
                "reason": f"淬炼神识 {amount} 香火",
            }

    return {
        "harvest_due": harvest_due,
        "harvest_checked": harvest_checked,
        "reason": "8 小时收割已检查，当前无可收香火" if harvest_checked else "当前无已启用且可执行的小世界动作",
    }


def _plan_cave_public_small_world_harvest(overview, *, now=None):
    now = float(now or time.time())
    small_world = overview.get("small_world") if isinstance(overview, dict) and isinstance(overview.get("small_world"), dict) else {}
    if not small_world or not small_world.get("available") or not small_world.get("has_world"):
        return {"reason": "小世界尚不可用"}
    if not state.get("small_world_harvest_enabled"):
        return {"reason": "自动收割香火未开启"}
    if not _cave_small_world_harvest_due(now):
        return {"reason": "MiniApp 收割尚未到 8 小时周期"}
    if small_world.get("can_harvest"):
        return {"action": "collect", "harvest_due": True, "reason": "MiniApp 8 小时收割到期"}
    return {
        "harvest_due": True,
        "harvest_checked": True,
        "reason": "8 小时收割已检查，当前无可收香火",
    }


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


def _record_cave_treasure_business_capture(capture_sink, result, *, source, now):
    result = dict(result or {})
    if not result.get("ok"):
        return {}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    settled_count = _parse_int(data.get("settled_count"), 0)
    if settled_count <= 0:
        return {}
    rows = data.get("results") if isinstance(data.get("results"), list) else []
    found_main = sum(
        1
        for item in rows
        if isinstance(item, dict) and bool(item.get("foundMain") or item.get("found_main"))
    )
    rewards, gains = _collect_materials(data)
    return append_business_capture(
        capture_sink,
        adapter_key="cave_treasure",
        detail={
            "settled_count": settled_count,
            "found_main": found_main,
            "gains": gains,
            "items": rewards,
        },
        source=source,
        created_at=now,
    )


def _tower_capture_store(now):
    day_key = get_day_key(now)
    path = CAVE_TREASURE_MINIAPP_CAPTURE_DIR / f"tower-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


_WILD_TRAINING_MODE_MAP = {
    "谨慎": "cautious",
    "均衡": "balanced",
    "深入": "deep",
}


def _server_epoch_seconds(value):
    try:
        timestamp = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0
    return max(0.0, timestamp)


def _wild_training_server_next_time(wild, *, now):
    wild = wild if isinstance(wild, dict) else {}
    remaining = max(0, _parse_int(wild.get("remaining_seconds"), 0))
    if remaining > 0:
        return float(now) + remaining
    ready_at = _server_epoch_seconds(wild.get("ready_at"))
    if ready_at > float(now):
        return ready_at
    if _parse_int(wild.get("daily_remaining"), 0) <= 0:
        reset_at = _server_epoch_seconds(wild.get("reset_at"))
        if reset_at > float(now):
            return reset_at
    return 0.0


def _wild_training_post_action_next_time(wild, action_result, *, now):
    next_time = _wild_training_server_next_time(wild, now=now)
    if next_time > float(now):
        return next_time
    wild = wild if isinstance(wild, dict) else {}
    action_result = action_result if isinstance(action_result, dict) else {}
    daily_limit = _parse_int(wild.get("daily_limit"), 0) or _parse_int(action_result.get("dailyLimit"), 0)
    daily_count = _parse_int(wild.get("daily_count"), -1)
    if daily_count < 0:
        daily_count = _parse_int(action_result.get("dailyCount"), 0)
    daily_remaining = _parse_int(wild.get("daily_remaining"), max(0, daily_limit - daily_count))
    if daily_remaining > 0 and bool(wild.get("available", True)):
        return float(now) + WILD_TRAINING_NO_COOLDOWN_FOLLOWUP_SEC
    return float(now) + 30 * 60


def _wild_training_action_summary(action_result):
    action_result = action_result if isinstance(action_result, dict) else {}
    title = str(action_result.get("title") or "野外历练").strip()
    cultivation_delta = _parse_int(action_result.get("cultivationDelta"), 0)
    rewards = {}
    for item in action_result.get("loot") or ():
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("itemId") or "").strip()
        amount = _parse_int(item.get("quantity", item.get("qty", item.get("count", 1))), 1)
        if name and amount > 0:
            rewards[name] = rewards.get(name, 0) + amount
    gains = {}
    for key, label in (("tianjiGain", "天机"), ("contributionGain", "贡献")):
        amount = _parse_int(action_result.get(key), 0)
        if amount:
            gains[label] = amount
    parts = []
    if cultivation_delta:
        parts.append(f"修为{cultivation_delta:+d}")
    for name, amount in sorted(gains.items()):
        if name == "修为" and cultivation_delta:
            continue
        if int(amount or 0):
            parts.append(f"{name}+{int(amount)}")
    for name, amount in sorted(rewards.items()):
        if int(amount or 0) > 0:
            parts.append(f"{name}x{int(amount)}")
    if action_result.get("fateProtected"):
        parts.append("改命脱险")
    message = str(action_result.get("message") or action_result.get("rawMessage") or "").strip()
    return title, "｜".join(parts) or message or "状态已更新", rewards, gains


def _record_cave_wild_training_state(identity_id, *, strategy, mode, wild, action_result, phase, now):
    payload = {
        "phase": str(phase or ""),
        "strategy": str(strategy or ""),
        "mode": str(mode or ""),
        "wild": dict(wild or {}),
        "result": {
            key: action_result.get(key)
            for key in (
                "ok", "completed", "type", "outcome", "title", "message", "rawMessage",
                "cultivationDelta", "successRate", "fateProtected", "dailyCount", "dailyLimit",
            )
            if key in (action_result or {})
        },
    }
    return record_miniapp_state(
        identity_id,
        "wild_training",
        payload,
        source="cave_dwelling_journey",
        source_id=f"wild_training:{int(identity_id)}:{stable_payload_digest(payload)}",
        now=now,
        outputs=("module_snapshot", "daily_counter", "inventory_delta"),
        replaces_commands=(".野外历练",),
    )


async def run_cave_public_wild_training(identity_id, public_entry_url, strategy, *, now=None):
    """Read the authoritative journey state and execute at most one wild-training action."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    strategy = str(strategy or "").strip()
    mode = _WILD_TRAINING_MODE_MAP.get(strategy, "")
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not mode:
        return {"ok": False, "message": "野外历练策略无效", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    with use_identity(identity_id):
        if not state.get("wild_training_enabled"):
            return {"ok": False, "message": "野外历练模块已关闭", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_wild_training_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            return {
                "ok": False,
                "message": f"洞府野外历练身份读取失败：{session.get('error') or 'unknown'}",
                "extra": {"phase": "session_failed"},
            }
        session_data = dict((session.get("result") or {}).get("data") or {})
        before_overview = session_data.get("overview") if isinstance(session_data.get("overview"), dict) else {}
        before_journey = before_overview.get("journey") if isinstance(before_overview.get("journey"), dict) else {}
        before_wild = before_journey.get("wild_experience") if isinstance(before_journey.get("wild_experience"), dict) else {}
        server_next_time = _wild_training_server_next_time(before_wild, now=now)
        available = bool(before_wild.get("available"))
        daily_remaining = _parse_int(before_wild.get("daily_remaining"), 0)
        if not before_wild:
            return {"ok": False, "message": "洞府游历页未返回野外历练状态", "extra": {"phase": "state_missing"}}
        if not available or daily_remaining <= 0 or server_next_time > now:
            next_time = server_next_time or (now + 30 * 60)
            _record_cave_wild_training_state(
                identity_id,
                strategy=strategy,
                mode=mode,
                wild=before_wild,
                action_result={},
                phase="cooldown",
                now=now,
            )
            return {
                "ok": True,
                "message": "MiniApp 野外历练尚未到期",
                "extra": {
                    "acted": False,
                    "phase": "cooldown",
                    "wild": before_wild,
                    "next_time": next_time,
                    "strategy": strategy,
                    "mode": mode,
                },
            }

        result = await run_cave_journey_action_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            action="wild_experience",
            mode=mode,
            player_id=session.get("player_id"),
            init_data=session.get("init_data") or "",
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_wild_training:{identity_id}",
        )
        raw = result.get("data") if isinstance(result.get("data"), dict) else {}
        after_overview = parse_cave_dwelling_overview(raw) if raw else {}
        action_player_error = _selected_player_error(after_overview, identity_id) if after_overview else "洞府动作回包缺少身份"
        after_journey = after_overview.get("journey") if isinstance(after_overview.get("journey"), dict) else {}
        after_wild = after_journey.get("wild_experience") if isinstance(after_journey.get("wild_experience"), dict) else {}
        if not after_wild:
            after_wild = dict(before_wild)
        action_result = raw.get("actionResult") if isinstance(raw.get("actionResult"), dict) else {}
        action_error = str(action_result.get("error") or "").strip()
        completed = (
            bool(result.get("ok"))
            and not action_player_error
            and bool(action_result.get("ok", True))
            and action_result.get("completed") is not False
        )
        next_time = _wild_training_post_action_next_time(after_wild, action_result, now=now)
        title, summary, rewards, gains = _wild_training_action_summary(action_result)
        phase = "completed" if completed else ("action_unknown" if not result.get("ok") else "blocked")
        _record_cave_wild_training_state(
            identity_id,
            strategy=strategy,
            mode=mode,
            wild=after_wild,
            action_result=action_result,
            phase=phase,
            now=now,
        )
        if completed and rewards:
            record_inventory_delta(
                identity_id,
                source="wild_training_miniapp",
                source_id=f"wild_training:{identity_id}:{stable_payload_digest(action_result)}",
                items=rewards,
                now=now,
                source_summary={"strategy": strategy, "title": title, "gains": gains},
            )
        return {
            "ok": completed,
            "message": f"{title}｜{summary}" if completed else (action_player_error or action_error or result.get("error") or summary or "野外历练未完成"),
            "extra": {
                "acted": True,
                "completed": completed,
                "phase": phase,
                "wild": after_wild,
                "before_wild": before_wild,
                "action_result": action_result,
                "next_time": next_time,
                "strategy": strategy,
                "mode": mode,
                "transport_ok": bool(result.get("ok")),
            },
        }


async def run_cave_public_small_world_sync(identity_id, public_entry_url, *, now=None, harvest_only=False):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    with use_identity(identity_id):
        next_time = float(state.get("next_small_world_time", 0) or 0)
        existing_next_time = next_time
        next_harvest_at = float(state.get("small_world_next_public_harvest_at", 0) or 0)
        harvest_due = _cave_small_world_harvest_due(now)
        if harvest_only and not state.get("small_world_harvest_enabled"):
            return {"ok": True, "message": "自动收割香火未开启，已跳过请求", "extra": {"skipped": True}}
        if harvest_only and not harvest_due:
            return {
                "ok": True,
                "message": "MiniApp 收割尚未到 8 小时周期，已跳过请求",
                "extra": {"skipped": True, "next_time": next_harvest_at},
            }
        if not harvest_only and next_time > now and not harvest_due:
            effective_next_time = min(
                item
                for item in (next_time, next_harvest_at if state.get("small_world_harvest_enabled") else 0)
                if item > 0
            )
            return {
                "ok": True,
                "message": "洞府小世界尚未到检查时间，已跳过请求",
                "extra": {"skipped": True, "next_time": effective_next_time},
            }
        last_request_at = float(state.get("small_world_last_public_request_at", 0) or 0)
        if last_request_at > 0 and now < last_request_at + CAVE_SMALL_WORLD_MIN_REQUEST_SEC:
            next_time = last_request_at + CAVE_SMALL_WORLD_MIN_REQUEST_SEC
            state["next_small_world_time"] = max(float(state.get("next_small_world_time", 0) or 0), next_time)
            if harvest_due:
                state["small_world_next_public_harvest_at"] = max(
                    float(state.get("small_world_next_public_harvest_at", 0) or 0),
                    next_time,
                )
            save_state()
            return {
                "ok": True,
                "message": "洞府小世界请求仍在最小间隔内，已跳过请求",
                "extra": {"skipped": True, "next_time": next_time},
            }
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        with use_identity(identity_id):
            state["small_world_last_public_request_at"] = float(now)
            save_state()
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_small_world_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            message = f"洞府小世界身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🌏 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=260)
            return {"ok": False, "message": message, "extra": {}}
        with use_identity(identity_id):
            session_data = dict((session.get("result") or {}).get("data") or {})
            result = await run_cave_small_world_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                init_data=session.get("init_data") or "",
                player_id=session.get("player_id"),
                action_planner=(
                    (lambda overview: _plan_cave_public_small_world_harvest(overview, now=now))
                    if harvest_only
                    else (lambda overview: _plan_cave_public_small_world_action(overview, now=now))
                ),
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_small_world:{identity_id}",
                initial_snapshot=session_data.get("raw") or {},
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
            harvest_was_due = bool(plan.get("harvest_due")) or _cave_small_world_harvest_due(now)
            harvest_checked = bool(plan.get("harvest_checked"))
            if action == "collect" and result.get("ok"):
                state["small_world_last_public_harvest_at"] = now
                state["small_world_next_public_harvest_at"] = now + CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC
            elif harvest_checked:
                state["small_world_next_public_harvest_at"] = now + CAVE_SMALL_WORLD_HARVEST_INTERVAL_SEC
            elif harvest_was_due and (harvest_only or not result.get("ok") or not small_world):
                state["small_world_next_public_harvest_at"] = now + CAVE_SMALL_WORLD_HARVEST_RETRY_SEC
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
                state["next_small_world_time"] = _cave_small_world_next_check_at(small_world, now)
                if action in {"miracle_sermon", "miracle_relief"}:
                    state["small_world_god_cooldown_until"] = now + CAVE_SMALL_WORLD_GOD_COOLDOWN_SEC
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
                    not harvest_only
                    and
                    not small_world.get("has_prayer")
                    and state.get("small_world_manifest_enabled")
                    and state.get("small_world_refresh_enabled")
                    and not plan.get("suppress_refresh")
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
                    state["next_small_world_time"] = _cave_small_world_next_check_at(small_world, now)
                    state["small_world_last_error"] = str(plan.get("reason") or "")
                    refresh_note = plan.get("reason") or "无需动作，6 小时后再查"
                faith = small_world.get("faith", 0)
                stability = small_world.get("stability", 0)
                prayer = small_world.get("prayer_title") or "无祈愿"
                message = f"洞府小世界已检查：信仰 {faith}｜稳定 {stability}｜{prayer}｜{refresh_note}"
            if harvest_only:
                state["next_small_world_time"] = _preserve_small_world_timer_after_harvest(
                    existing_next_time,
                    small_world,
                    now,
                )
            save_state()
        await send_audit_log(
            f"🌏 {message}",
            scope="identity",
            send_as_id=identity_id,
            priority="high" if resource_blocked else ("normal" if action or not result.get("ok") else "low"),
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
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    identity_error = _public_entry_account_identity_error(identity_id)
    if identity_error:
        return {"ok": False, "message": identity_error, "extra": {}}
    if _cave_treasure_unknown_hold(identity_id, now):
        message = "洞府寻宝今日存在结果未知动作，已冻结自动重试至次日"
        await send_audit_log(f"🕳️ {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
        return {
            "ok": True,
            "message": message,
            "extra": {"daily_exhausted": True, "skipped": "outcome_unknown_hold"},
        }
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_treasure_start:{identity_id}",
        )
        if not session.get("ok"):
            message = f"洞府寻宝身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🕳️ {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=260)
            return {"ok": False, "message": message, "extra": {}}
        capture_sink = _capture_store(now)
        capture_source = f"cave_public_treasure:{identity_id}"
        result = await run_cave_treasure_miniapp_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            init_data=session.get("init_data") or "",
            player_id=session.get("player_id"),
            max_steps=CAVE_TREASURE_MANUAL_MAX_STEPS,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        _record_cave_treasure_business_capture(capture_sink, result, source=capture_source, now=now)
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        inventory_record = _record_cave_treasure_inventory_delta(identity_id, result, now=now)
        state_record = _record_cave_treasure_miniapp_state(identity_id, result, now=now)
        summary = _format_cave_treasure_summary(result)
        message = f"洞府寻宝公共入口：{summary}"
        settled_count = _parse_int((result.get("data") or {}).get("settled_count"), 0)
        rewards, gains = _collect_materials(data or {})
        changed = bool(inventory_record.get("changed")) or settled_count > 0
        await send_audit_log(
            f"🕳️ {message}",
            scope="identity",
            send_as_id=identity_id,
            priority="normal" if changed or not result.get("ok") else "low",
            limit=260,
        )
        return {
            "ok": bool(result.get("ok")),
            "message": message,
            "extra": {
                "inventory_record_key": inventory_record.get("record_key", ""),
                "state_record_key": state_record.get("record_key", ""),
                "games_used": _parse_int(state.get("games_used"), 0),
                "games_limit": _parse_int(state.get("games_limit"), 0),
                "settled_count": settled_count,
                "gains": gains,
                "rewards": rewards,
                "daily_exhausted": (
                    str(result.get("status") or "").strip() == "daily_limit"
                    or str(result.get("status") or "").strip() == "result_unknown"
                    or (
                        _parse_int(state.get("games_limit"), 0) > 0
                        and _parse_int(state.get("games_used"), 0) >= _parse_int(state.get("games_limit"), 0)
                    )
                ),
            },
        }


async def run_cave_public_trial(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_trial_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            message = f"洞府天机试炼入口读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
            return {"ok": False, "message": message, "extra": {}}
        dwelling_init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        overview = cave_data.get("overview") if isinstance(cave_data.get("overview"), dict) else {}
        if not cave_result.get("ok"):
            message = f"洞府天机试炼入口读取失败：{cave_result.get('error') or cave_result.get('status') or 'unknown'}"
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
                player_id=selected_player_id,
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
        capture_sink = _trial_miniapp_capture_store(now)
        capture_source = f"cave_public_trial:{identity_id}"
        result = await run_trial_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            init_data=dwelling_init_data,
            player_id=selected_player_id,
            max_rounds=99,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        _record_trial_business_capture(capture_sink, result, source=capture_source, now=now)
        summary = _format_trial_summary(result)
        message = f"洞府天机试炼公共入口：{summary}"
        completed_ok = bool(result.get("ok")) or str(result.get("status") or "") == "daily_limit"
        rewards, gains = _trial_batch_materials(result)
        settled_count = _parse_int(
            result.get("settled_count") or (result.get("data") or {}).get("settled_count"),
            0,
        )
        await send_audit_log(f"🧪 {message}", scope="identity", send_as_id=identity_id, priority="low" if completed_ok else "normal", limit=260)
        return {
            "ok": completed_ok,
            "message": message,
            "extra": {
                "trial_title": launch.get("title", ""),
                "settled_count": settled_count,
                "gains": gains,
                "rewards": rewards,
            },
        }


async def run_cave_public_tower(identity_id, public_entry_url, *, now=None):
    """Run one identity's daily tower challenge through the dwelling entry."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tower_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            message = f"洞府琉璃问心塔身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
            return {"ok": False, "message": message, "extra": {}}
        init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        external_app = _find_tower_external_app_in_cave_payload(raw)
        if not external_app or not external_app.get("available"):
            message = "洞府公共入口未开放琉璃问心塔"
            await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}

        launch = {}
        if external_app.get("action"):
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=selected_player_id,
                init_data=init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_tower_external:{identity_id}",
            )
            if not external_result.get("ok"):
                message = f"洞府琉璃问心塔动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}"
                await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
                return {"ok": False, "message": message, "extra": {}}
            launch = _find_tower_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _find_tower_launch_in_cave_payload(external_app)
        if not launch:
            message = "洞府琉璃问心塔入口未返回可用 URL"
            await send_audit_log(f"🗼 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=220)
            return {"ok": False, "message": message, "extra": {}}

        result = await run_tower_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            init_data=init_data,
            capture_sink=_tower_capture_store(now),
            capture_source=f"cave_public_tower:{identity_id}",
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        tower_state = data.get("state") if isinstance(data.get("state"), dict) else {}
        replay = data.get("replay") if isinstance(data.get("replay"), dict) else {}
        gains = dict(data.get("gains") or {})
        rewards = dict(data.get("rewards") or {})
        phase = "completed" if result.get("ok") else "blocked"
        record_miniapp_state(
            identity_id,
            "tower",
            {
                "phase": phase,
                "status": result.get("status") or "",
                "challenged": bool(data.get("challenged")),
                "dao_name": tower_state.get("dao_name") or "",
                "today_highest": tower_state.get("today_highest", 0),
                "record_highest": tower_state.get("record_highest", 0),
                "cleared_count": replay.get("cleared_count", 0),
                "end_floor": replay.get("end_floor", 0),
                "failed_floor": replay.get("failed_floor", 0),
                "gains": gains,
                "rewards": rewards,
                "error": result.get("error") or "",
            },
            source="cave_public_tower",
            source_id=f"tower:{identity_id}:{int(now)}",
            now=now,
            outputs=("daily_counter", "tower_progress", "rewards"),
            replaces_commands=(".闯塔", ".继续闯塔"),
        )
        if result.get("status") == "done_today":
            message = "洞府琉璃问心塔：今日已完成或已止步，未重铸道心"
        elif result.get("ok"):
            message = (
                f"洞府琉璃问心塔：通过 {replay.get('cleared_count', 0)} 层"
                f"｜止步 {replay.get('failed_floor') or '未止步'} 层"
                f"｜修为 {format_tower_delta(gains.get('修为', 0))}"
                f"｜塔印 {format_tower_delta(gains.get('塔印', 0))}"
            )
        else:
            message = f"洞府琉璃问心塔失败：{result.get('error') or result.get('status') or 'unknown'}"
        await send_audit_log(
            f"🗼 {message}",
            scope="identity",
            send_as_id=identity_id,
            priority="low" if result.get("ok") else "normal",
            limit=280,
        )
        return {
            "ok": bool(result.get("ok")),
            "message": message,
            "extra": {
                "status": result.get("status") or "",
                "state": tower_state,
                "replay": replay,
                "gains": gains,
                "rewards": rewards,
            },
        }


async def run_cave_public_fishing(identity_id, public_entry_url, *, now=None):
    """Run fishing for a selected dwelling identity without a channel group command."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_fishing_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            message = f"洞府钓鱼身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"🎣 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=260)
            return {"ok": False, "message": message, "extra": {}}

        dwelling_init_data = str(session.get("init_data") or "")
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        raw = cave_data.get("raw") if isinstance(cave_data.get("raw"), dict) else {}
        external_app = _find_fishing_external_app_in_cave_payload(raw)
        if not external_app:
            with use_identity(identity_id):
                state["next_fishing_time"] = fishing_behavior.next_fishing_reset_timestamp(
                    now,
                    _fishing_reset_jitter_sec(identity_id),
                )
                state["fishing_last_result"] = "未开放灵溪垂钓，今日跳过"
                state["fishing_last_error"] = ""
                save_state()
            message = "该身份未开放灵溪垂钓，今日跳过"
            await send_audit_log(f"🎣 {message}", scope="identity", send_as_id=identity_id, priority="low", limit=220)
            return {"ok": True, "message": message, "extra": {"skipped": "entry_missing"}}
        if not external_app.get("available"):
            with use_identity(identity_id):
                state["next_fishing_time"] = fishing_behavior.next_fishing_reset_timestamp(
                    now,
                    _fishing_reset_jitter_sec(identity_id),
                )
                state["fishing_last_result"] = "未持有鱼竿，今日跳过"
                state["fishing_last_error"] = ""
                save_state()
            message = "未持有鱼竿，今日跳过灵溪垂钓"
            await send_audit_log(f"🎣 {message}", scope="identity", send_as_id=identity_id, priority="low", limit=220)
            return {"ok": True, "message": message, "extra": {"skipped": "rod_missing"}}

        launch = {}
        if external_app.get("action"):
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action="fishing",
                player_id=selected_player_id,
                init_data=dwelling_init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_fishing_external:{identity_id}",
            )
            if not external_result.get("ok"):
                message = f"洞府钓鱼动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}"
                await send_audit_log(f"🎣 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=260)
                return {"ok": False, "message": message, "extra": {}}
            launch = extract_fishing_miniapp_launch_from_dwelling_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = extract_fishing_miniapp_launch_from_dwelling_payload({
                "account": {"externalApps": {"groups": [{"apps": [external_app]}]}},
            })
        if not launch:
            message = "洞府钓鱼入口已请求，但未返回可用 URL"
            await send_audit_log(f"🎣 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=240)
            return {"ok": False, "message": message, "extra": {}}

        with use_identity(identity_id):
            max_rounds = _remaining_miniapp_chain_rounds(now)
            pond_choice = str(state.get("fishing_pond") or "")
            bait_choice = str(state.get("fishing_bait") or "")
        capture_sink = _fishing_miniapp_capture_store(now)
        capture_source = f"cave_public_fishing:{identity_id}"
        result = await run_fishing_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            init_data=dwelling_init_data,
            max_rounds=max_rounds,
            pond_choice=pond_choice,
            bait_choice=bait_choice,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        _record_fishing_business_capture(capture_sink, result, source=capture_source, now=now)
        terminal_message = ""
        with use_identity(identity_id):
            public_only_bait_missing = bool(
                str(result.get("status") or "").strip() == "bait_missing"
                and not state.get("fishing_enabled")
            )
            if public_only_bait_missing:
                summary = _apply_fishing_miniapp_result(result, time.time())
                state["next_fishing_time"] = fishing_behavior.next_fishing_reset_timestamp(
                    now,
                    _fishing_reset_jitter_sec(identity_id),
                )
                state["fishing_last_result"] = f"{summary}｜无可用鱼饵，今日跳过"
                state["fishing_last_error"] = ""
                terminal_message = state["fishing_last_result"]
                save_state()
        if public_only_bait_missing:
            message = f"洞府灵溪垂钓公共入口：{terminal_message or '无可用鱼饵，今日跳过'}"
            await send_audit_log(
                f"🎣 {message}",
                scope="identity",
                send_as_id=identity_id,
                priority="low",
                limit=220,
            )
            with use_identity(identity_id):
                await _send_fishing_daily_completion_summary(time.time())
            return {
                "ok": True,
                "message": message,
                "extra": {
                    "fishing_title": launch.get("title") or external_app.get("title") or "灵溪垂钓",
                    "player_id": selected_player_id,
                    "skipped": "bait_missing",
                    "terminal_skip": True,
                },
            }
        with use_identity(identity_id):
            summary = _apply_fishing_miniapp_result(result, time.time())
        completed_ok = bool(result.get("ok")) or str(result.get("status") or "") == "daily_limit"
        message = f"洞府灵溪垂钓公共入口：{summary}"
        await send_audit_log(
            f"🎣 {message}",
            scope="identity",
            send_as_id=identity_id,
            priority="low" if completed_ok else "normal",
            limit=420,
        )
        if completed_ok:
            with use_identity(identity_id):
                await _send_fishing_daily_completion_summary(time.time())
        return {
            "ok": completed_ok,
            "message": message,
            "extra": {
                "fishing_title": launch.get("title") or external_app.get("title") or "灵溪垂钓",
                "player_id": selected_player_id,
                "daily_exhausted": str(result.get("status") or "").strip() == "daily_limit",
            },
        }


async def run_cave_public_yuanying(identity_id, public_entry_url, *, now=None):
    """Run the one safe Tianjige command exposed by the public dwelling entry."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    with use_identity(identity_id):
        if not state.get("yuanying_enabled"):
            return {"ok": False, "message": "元婴模块已关闭", "extra": {}}
        next_yuanying_time = float(state.get("next_yuanying_time", 0) or 0)
        block_reason = yuanying.get_yuanying_block_reason(now)
    if next_yuanying_time > now:
        return {"ok": False, "message": f"元婴尚未到出窍窗口：{block_reason or '等待中'}", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tianjige_start:{identity_id}",
        )
        if not session.get("ok"):
            reason = session.get("error") or "unknown"
            message = f"洞府天机阁身份读取失败：{reason}"
            await send_audit_log(f"👶 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=280)
            return {"ok": False, "message": message, "extra": {}}
        init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")

        status_result = await run_cave_tianjige_command_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            command=yuanying.CMD_YUANYING_STATUS,
            init_data=init_data,
            player_id=selected_player_id,
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_tianjige_yuanying_status:{identity_id}",
        )
        status_data = status_result.get("data") if isinstance(status_result.get("data"), dict) else {}
        status_sync = await sync_cave_tianjige_yuanying_result(
            identity_id,
            status_data,
            now=now,
            command=yuanying.CMD_YUANYING_STATUS,
        )
        status_ok = bool(status_result.get("ok")) and _cave_tianjige_action_succeeded(status_data)
        if not status_ok or not status_sync.get("handled"):
            reply_message = str(status_sync.get("message") or "").strip()
            message = f"洞府天机阁元婴状态未确认：{reply_message or status_result.get('error') or status_result.get('status') or 'unknown'}"
            await send_audit_log(f"👶 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=320)
            return {"ok": False, "message": message, "extra": {"status_sync": status_sync}}
        if not status_sync.get("ready"):
            message = f"洞府天机阁元婴状态：{status_sync.get('message') or '已同步，当前无需出窍'}"
            await send_audit_log(f"👶 {message}", scope="identity", send_as_id=identity_id, priority="low", limit=320)
            return {"ok": True, "message": message, "extra": {"status_sync": status_sync, "launched": False}}

        result = await run_cave_tianjige_command_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            command=yuanying.CMD_YUANYING,
            init_data=init_data,
            player_id=selected_player_id,
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_tianjige_yuanying:{identity_id}",
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        sync_result = await sync_cave_tianjige_yuanying_result(
            identity_id,
            data,
            now=now,
            command=yuanying.CMD_YUANYING,
        )
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
            priority="normal",
            limit=320,
        )
        return {
            "ok": bool(action_ok and sync_result.get("handled")),
            "message": message,
            "extra": {"status_sync": status_sync, "sync": sync_result, "launched": True},
        }


async def run_cave_public_tianti_status(identity_id, public_entry_url, *, now=None):
    """Read `.天阶状态` through Tianjige and calibrate the existing reducer."""
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    with use_identity(identity_id):
        if not state.get("tianti_enabled"):
            return {"ok": False, "message": "天阶模块已关闭", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}

    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tianti_status_start:{identity_id}",
        )
        if not session.get("ok"):
            message = f"洞府天机阁天阶状态身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"☁️ {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=280)
            return {"ok": False, "message": message, "extra": {}}

        result = await run_cave_tianjige_command_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            command=CMD_TIANTI_STATUS,
            init_data=session.get("init_data") or "",
            player_id=session.get("player_id"),
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_tianjige_tianti_status:{identity_id}",
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        message = extract_cave_tianjige_command_message(data)
        if not result.get("ok") or not message:
            error_text = result.get("error") or result.get("status") or "天机阁未返回天阶状态文案"
            final_message = f"洞府天机阁天阶状态未确认：{error_text}"
            await send_audit_log(f"☁️ {final_message}", scope="identity", send_as_id=identity_id, priority="normal", limit=300)
            return {"ok": False, "message": final_message, "extra": {"raw_message": message}}

        with use_identity(identity_id):
            sync_result = tianti.sync_tianti_miniapp_status(message, now=now)
        if not sync_result.get("handled"):
            final_message = "洞府天机阁天阶状态回包未匹配现有解析器，保留原状态"
            await send_audit_log(f"☁️ {final_message}", scope="identity", send_as_id=identity_id, priority="normal", limit=300)
            return {"ok": False, "message": final_message, "extra": {"raw_message": message}}

        final_message = "洞府天机阁天阶状态已同步（只读，不触发登阶）"
        await send_audit_log(
            f"☁️ {final_message}｜进度 {sync_result.get('payload', {}).get('progress_current', 0)}/{sync_result.get('payload', {}).get('progress_total', 0)}",
            scope="identity",
            send_as_id=identity_id,
            priority="low",
            limit=300,
        )
        return {"ok": True, "message": final_message, "extra": {"sync": sync_result}}


def _sync_cave_tianjige_read_only_message(identity_id, command, message, *, now):
    """Replay supported read-only Tianjige panels through their existing reducer."""
    if command not in {".我的阴罗幡", ".我的侍妾"}:
        return {"supported": False, "handled": False, "summary": {}}

    with use_identity(identity_id):
        if command == ".我的侍妾":
            sync_result = concubine.sync_concubine_miniapp_status(message, now)
            summary = dict(sync_result.get("summary") or {})
            detail = ""
            if sync_result.get("handled"):
                detail = (
                    f"侍妾 {summary.get('name') or '未知'}"
                    f"｜情缘 {summary.get('affinity', 0)}"
                    f"｜位置 {summary.get('location') or '未知'}"
                )
            return {
                "supported": True,
                "handled": bool(sync_result.get("handled")),
                "reason": str(sync_result.get("reason") or ""),
                "summary": summary,
                "detail": detail,
            }
        handled = bool(
            yinluo.apply_yinluo_passive(
                message,
                now=now,
                family="yinluo_banner",
                event_context={"source": "cave_tianjige_read_only"},
            )
        )
        if handled:
            yinluo.save_state()
        observed = dict((yinluo.get_yinluo_ui_state(now=now).get("observed") or {}))
    summary = {
        "sha_current": int(observed.get("sha_current", 0) or 0),
        "sha_max": int(observed.get("sha_max", 0) or 0),
        "ready_slots": list(observed.get("ready_slot_numbers") or []),
        "refining_slots": list(observed.get("refining_slot_numbers") or []),
    }
    return {
        "supported": True,
        "handled": handled,
        "summary": summary,
        "detail": (
            f"煞气 {summary.get('sha_current', 0)}/{summary.get('sha_max', 0)}"
            f"｜精华槽 {len(summary.get('ready_slots') or [])}"
            f"｜炼化中 {len(summary.get('refining_slots') or [])}"
        ),
    }


def _unbridged_cave_tianjige_observation(command, message):
    """Keep an unowned panel observable without presenting it as synced state."""
    raw_message = str(message or "").strip()
    first_line = re.sub(r"\s+", " ", raw_message.splitlines()[0] if raw_message else "").strip()
    return {
        "command": str(command or "").strip(),
        "message_digest": stable_payload_digest(raw_message),
        "message_length": len(raw_message),
        "first_line": first_line[:120],
    }


async def run_cave_public_tianjige_read_only(identity_id, public_entry_url, command, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    try:
        normalized_command = str(command or "").strip()
        if normalized_command not in CAVE_TIANJIGE_READ_ONLY_COMMANDS:
            return {"ok": False, "message": "洞府天机阁只读命令不在白名单", "extra": {}}
    except Exception:
        return {"ok": False, "message": "洞府天机阁只读命令无效", "extra": {}}
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tianjige_read_only_start:{identity_id}",
        )
        if not session.get("ok"):
            message = f"洞府天机阁只读身份读取失败：{session.get('error') or 'unknown'}"
            await send_audit_log(f"📖 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=280)
            return {"ok": False, "message": message, "extra": {}}
        result = await run_cave_tianjige_command_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            command=normalized_command,
            init_data=session.get("init_data") or "",
            player_id=session.get("player_id"),
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_tianjige_read_only:{identity_id}",
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        message = extract_cave_tianjige_command_message(data)
        if not result.get("ok") or not message:
            final_message = f"洞府天机阁只读未确认：{result.get('error') or result.get('status') or '无可识别回包'}"
            await send_audit_log(f"📖 {final_message}", scope="identity", send_as_id=identity_id, priority="normal", limit=300)
            return {"ok": False, "message": final_message, "extra": {"raw_message": message}}
        if normalized_command == ".我的灵兽":
            observation = _unbridged_cave_tianjige_observation(normalized_command, message)
            final_message = "洞府天机阁灵兽面板已读取，但本地尚无对应 reducer；仅观察，不更新放养状态"
            await send_audit_log(
                f"📖 {final_message}｜首行={observation['first_line'] or '-'}｜摘要={observation['message_digest']}",
                scope="identity",
                send_as_id=identity_id,
                priority="normal",
                limit=320,
            )
            return {
                "ok": False,
                "message": final_message,
                "extra": {"command": normalized_command, "observation": observation},
            }
        sync_result = _sync_cave_tianjige_read_only_message(
            identity_id,
            normalized_command,
            message,
            now=now,
        )
        if sync_result.get("supported"):
            if not sync_result.get("handled"):
                final_message = f"洞府天机阁只读回包未匹配现有解析器：{normalized_command}"
                first_line = re.sub(r"\s+", " ", message.splitlines()[0] if message else "").strip()[:120]
                console_log(
                    f"📖 {final_message}｜reason={sync_result.get('reason') or 'unknown'}"
                    f"｜first_line={first_line or '-'}"
                )
                await send_audit_log(
                    f"📖 {final_message}",
                    scope="identity",
                    send_as_id=identity_id,
                    priority="normal",
                    limit=300,
                )
                return {
                    "ok": False,
                    "message": final_message,
                    "extra": {"command": normalized_command, "raw_message": message},
                }
            summary = dict(sync_result.get("summary") or {})
            final_message = f"洞府天机阁只读状态已同步：{normalized_command}"
            detail = str(sync_result.get("detail") or "").strip()
            await send_audit_log(
                f"📖 {final_message}{f'｜{detail}' if detail else ''}",
                scope="identity",
                send_as_id=identity_id,
                priority="low",
                limit=320,
            )
            return {
                "ok": True,
                "message": final_message,
                "extra": {"command": normalized_command, "sync": summary},
            }
        final_message = f"洞府天机阁只读｜{normalized_command}：{message}"
        await send_audit_log(f"📖 {final_message}", scope="identity", send_as_id=identity_id, priority="low", limit=360)
        return {"ok": True, "message": final_message, "extra": {"command": normalized_command, "raw_message": message}}


async def run_cave_public_stargazer(identity_id, public_entry_url, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    with use_identity(identity_id):
        if not state.get("stargazer_enabled"):
            return {"ok": False, "message": "观星台模块已关闭", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_stargazer_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            return {"ok": False, "message": f"洞府观星台身份读取失败：{session.get('error') or 'unknown'}", "extra": {}}
        init_data = session.get("init_data") or ""
        selected_player_id = session.get("player_id")
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        overview = cave_data.get("overview") if isinstance(cave_data.get("overview"), dict) else {}
        external_app = _find_stargazer_external_app_in_cave_payload(cave_data.get("raw") or {})
        if not external_app or not external_app.get("available"):
            with use_identity(identity_id):
                state["next_stargazer_panel_time"] = now + 6 * 3600
                state["stargazer_followup_due_at"] = 0
                state["stargazer_queued_action"] = ""
                state["stargazer_last_action"] = "public_entry_unavailable"
                save_state()
            return {
                "ok": True,
                "message": "该身份未开放观星台，已跳过并于 6 小时后复查",
                "extra": {"skipped": "entry_missing"},
            }
        launch = {}
        if external_app.get("action") and str(external_app.get("url") or "").strip() in {"", "#"}:
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=selected_player_id,
                init_data=init_data,
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_stargazer_external:{identity_id}",
            )
            if not external_result.get("ok"):
                return {
                    "ok": False,
                    "message": f"洞府观星台动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}",
                    "extra": {},
                }
            launch = _find_stargazer_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _stargazer_launch_from_external_app(external_app)
        if not launch:
            return {"ok": False, "message": "洞府观星台入口未返回可用 URL", "extra": {}}
        with use_identity(identity_id):
            star_choice = stargazer.get_stargazer_star_choice()
        capture_sink = stargazer._stargazer_miniapp_capture_store(now)
        capture_source = f"cave_public_stargazer:{identity_id}"
        result = await run_stargazer_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            star_choice=star_choice,
            init_data=init_data,
            player_id=selected_player_id,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        result = dict(result or {})
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        action_counts = result_data.get("action_counts") if isinstance(result_data.get("action_counts"), dict) else {}
        item_deltas = result_data.get("item_deltas") if isinstance(result_data.get("item_deltas"), dict) else {}
        collect_count = _parse_int(action_counts.get("collect"), 0)
        if result.get("ok") and collect_count > 0:
            append_business_capture(
                capture_sink,
                adapter_key="stargazer",
                detail={"collect_count": collect_count, "items": item_deltas},
                source=capture_source,
                created_at=now,
            )
        with use_identity(identity_id):
            handled = await stargazer._finish_stargazer_miniapp_result(result, now, star_choice=star_choice)
        return {
            "ok": bool(handled and result.get("ok")),
            "message": f"洞府观星台：{result.get('status') or ('完成' if handled else '未处理')}",
            "extra": {
                "title": launch.get("title", ""),
                "action_counts": dict(result_data.get("action_counts") or {}),
                "rewards": dict(result_data.get("item_deltas") or {}),
            },
        }


async def run_cave_public_tree(
    identity_id,
    public_entry_url,
    *,
    now=None,
    day_key="",
    op_id="",
    score_profiles=None,
):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    eligible, reason = tree_runtime.check_tree_miniapp_eligibility(identity_id, enabled=True)
    if not eligible:
        return {"ok": False, "message": reason, "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_tree_start:{identity_id}",
            include_details=True,
        )
        if not session.get("ok"):
            return {"ok": False, "message": f"洞府灵树身份读取失败：{session.get('error') or 'unknown'}", "extra": {}}
        cave_result = dict(session.get("result") or {})
        cave_data = dict(cave_result.get("data") or {})
        external_app = _find_tree_external_app_in_cave_payload(cave_data.get("raw") or {})
        if not external_app or not external_app.get("available"):
            return {"ok": False, "message": "洞府外府未开放落云灵树入口", "extra": {}}
        launch = {}
        if external_app.get("action") and str(external_app.get("url") or "").strip() in {"", "#"}:
            external_result = await run_cave_external_action_production_flow(
                identity_id,
                token=token,
                webview_url=webview_url,
                action=external_app["action"],
                player_id=session.get("player_id"),
                init_data=session.get("init_data") or "",
                capture_sink=_capture_store(now),
                capture_source=f"cave_public_tree_external:{identity_id}",
            )
            if not external_result.get("ok"):
                return {
                    "ok": False,
                    "message": f"洞府落云灵树动态入口获取失败：{external_result.get('error') or external_result.get('status') or 'unknown'}",
                    "extra": {},
                }
            launch = _find_tree_launch_in_cave_payload(external_result.get("data") or {})
        elif external_app.get("url"):
            launch = _tree_launch_from_external_app(external_app)
        if not launch:
            return {"ok": False, "message": "洞府落云灵树入口未返回可用 URL", "extra": {}}
        result = await tree_runtime.run_tree_miniapp_daily_direct(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            init_data=session.get("init_data") or "",
            day_key=day_key or get_day_key(now),
            op_id=op_id,
            score_profiles=score_profiles,
            now=now,
        )
        return {
            "ok": bool(result.get("ok")),
            "message": f"洞府落云灵树：{result.get('status') or ('完成' if result.get('ok') else '未完成')}",
            "extra": {"title": launch.get("title", ""), "result": result},
        }


async def run_cave_public_deep_retreat_action(identity_id, public_entry_url, action, *, now=None):
    identity_id = _identity_id(identity_id)
    now = float(now or time.time())
    action = str(action or "").strip()
    if action not in {"status", "start", "settle", "force"}:
        return {"ok": False, "message": "洞府闭关动作仅允许 status/start/settle/force", "extra": {}}
    if identity_id <= 0:
        return {"ok": False, "message": "身份不存在", "extra": {}}
    if not is_cave_public_identity_available(identity_id):
        return {"ok": False, "message": "身份已停用", "extra": {}}
    if not _public_entry_allowed():
        return {"ok": False, "message": "全局暂停来源不允许洞府公共入口 MiniApp HTTP", "extra": {}}
    token, webview_url, error = _parse_public_cave_entry_url(public_entry_url)
    if error:
        return {"ok": False, "message": error, "extra": {}}
    lock = _public_entry_lock(identity_id)
    if lock.locked():
        return {"ok": False, "message": "洞府公共入口操作执行中", "extra": {}}
    async with lock:
        session = await _load_cave_public_identity_session(
            identity_id,
            token,
            webview_url,
            now=now,
            capture_source=f"cave_public_deep_retreat_start:{identity_id}",
        )
        if not session.get("ok"):
            message = f"洞府闭关身份读取失败：{session.get('error') or 'unknown'}"
            _record_cave_deep_retreat_state(
                identity_id,
                action,
                {"ok": False, "status": "session_failed", "error": session.get("error") or "unknown", "data": {}},
                {"handled": False, "reason": "session_failed", "phase": ""},
                now=now,
            )
            await send_audit_log(f"🧘 {message}", scope="identity", send_as_id=identity_id, priority="normal", limit=260)
            return {"ok": False, "message": message, "extra": {}}
        result = await run_cave_deep_seclusion_action_production_flow(
            identity_id,
            token=token,
            webview_url=webview_url,
            action=action,
            init_data=session.get("init_data") or "",
            capture_sink=_capture_store(now),
            capture_source=f"cave_public_deep_retreat:{identity_id}:{action}",
        )
        sync_result = await sync_cave_deep_seclusion_action_result(identity_id, action, result.get("data") or {}, now=now)
        if action in {"status", "settle"} and result.get("ok") and not sync_result.get("handled"):
            with use_identity(identity_id):
                state["next_deep_retreat_time"] = max(
                    float(state.get("next_deep_retreat_time", 0) or 0),
                    now + CAVE_DEEP_STATUS_RECHECK_SEC,
                )
                save_state()
        record = _record_cave_deep_retreat_state(identity_id, action, result, sync_result, now=now)
        if not result.get("ok"):
            message = f"洞府闭关 {action} 失败：{result.get('error') or result.get('status') or 'unknown'}"
        else:
            phase = (sync_result or {}).get("phase") or "-"
            handled = "已同步" if (sync_result or {}).get("handled") else "未改状态"
            recheck = ""
            if not (sync_result or {}).get("handled"):
                if action == "status":
                    recheck = "｜30 分钟后保守复查"
                elif action == "settle":
                    recheck = "｜30 分钟后改查状态"
            message = f"洞府闭关 {action} 完成：{handled}｜阶段 {phase}{recheck}"
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
    identity_available = is_cave_public_identity_available(identity_id)
    if (not global_enabled and not maintenance_miniapp_allowed) or not identity_available:
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
        capture_sink = _capture_store(now)
        capture_source = f"cave_treasure_runtime:{identity_id}:{int(result_msg_id or getattr(event, 'id', 0) or 0)}"
        result = await run_cave_treasure_miniapp_production_flow(
            identity_id,
            token=launch.get("token"),
            webview_url=launch.get("webview_url"),
            max_steps=CAVE_TREASURE_MANUAL_MAX_STEPS,
            capture_sink=capture_sink,
            capture_source=capture_source,
        )
        _record_cave_treasure_business_capture(capture_sink, result, source=capture_source, now=now)
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
    "extract_cave_deep_seclusion_state",
    "extract_cave_tianjige_command_message",
    "handle_cave_treasure_miniapp_entry",
    "is_cave_public_entry_busy",
    "revoke_cave_treasure_miniapp_manual_run",
    "run_cave_public_deep_retreat_action",
    "run_cave_public_fishing",
    "run_cave_public_small_world_sync",
    "run_cave_public_stargazer",
    "run_cave_public_tianjige_read_only",
    "run_cave_public_tianti_status",
    "run_cave_public_tower",
    "run_cave_public_treasure",
    "run_cave_public_trial",
    "run_cave_public_wild_training",
    "run_cave_public_yuanying",
    "sync_cave_deep_seclusion_action_result",
    "sync_cave_tianjige_yuanying_result",
    "_find_trial_launch_in_cave_payload",
    "_find_tower_external_app_in_cave_payload",
    "_find_tower_launch_in_cave_payload",
    "_cave_treasure_inventory_items",
    "_record_cave_deep_retreat_state",
    "_record_cave_small_world_state",
    "_record_cave_treasure_miniapp_state",
]
