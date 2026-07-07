import asyncio
import re
import time
from pathlib import Path

from ..runtime import send_audit_log
from ..state import get_current_identity_id, get_global_enabled, get_identity_enabled, get_send_as_profile
from ..timing import get_day_key
from ..webapp_core import MiniAppCaptureStore
from .cave_treasure_miniapp import extract_cave_treasure_miniapp_launch, run_cave_treasure_miniapp_production_flow


CAVE_TREASURE_MANUAL_AUTH_TTL_SEC = 10 * 60
CAVE_TREASURE_MANUAL_MAX_STEPS = 48
CAVE_TREASURE_MINIAPP_CAPTURE_DIR = Path(__file__).resolve().parents[2] / "data" / "state" / "miniapp_capture"

_MANUAL_AUTH_UNTIL = {}
_RUN_LOCKS = {}
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
    "logs",
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


def _capture_store(now):
    day_key = get_day_key(now)
    path = CAVE_TREASURE_MINIAPP_CAPTURE_DIR / f"cave_treasure-{day_key}.jsonl"
    return MiniAppCaptureStore(path, keep_memory=False)


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
    identity_enabled = get_identity_enabled(identity_id)
    if not global_enabled or not identity_enabled:
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
            "🕳️ 洞府寻宝 MiniApp 接管入口，开始 WebView/HTTP 流程。",
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
        priority = "low" if dict(result or {}).get("ok") else "normal"
        await send_audit_log(f"🕳️ 洞府寻宝结果｜{summary}", scope="identity", priority=priority, limit=260)
        return True


__all__ = [
    "CAVE_TREASURE_MANUAL_AUTH_TTL_SEC",
    "CAVE_TREASURE_MANUAL_MAX_STEPS",
    "authorize_cave_treasure_miniapp_manual_run",
    "handle_cave_treasure_miniapp_entry",
    "revoke_cave_treasure_miniapp_manual_run",
]
