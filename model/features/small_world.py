import re
import time

from ..config import CMD_SMALL_WORLD_PREACH, SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_enabled, get_identity_ids, get_send_as_tags, state
from ..timing import fmt_abs_ts, fmt_remaining

SMALL_WORLD_TARGET_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"
RE_SMALL_WORLD_DISASTER = re.compile(r"【小世界·天降浩劫】")
RE_SMALL_WORLD_TARGET_TAG = re.compile(rf"道友\s*@({SMALL_WORLD_TARGET_TAG_PATTERN})\s*的小世界遭遇")
RE_SMALL_WORLD_FAITH_DAMAGE = re.compile(r"惨重代价\s*[:：]\s*信仰(?:崩塌|动摇)\s*-\s*\d+\s*点")
RE_SMALL_WORLD_PREACH_PANEL = re.compile(r"【神音浩荡】")
RE_SMALL_WORLD_FAITH_VALUE = re.compile(r"信仰值大幅提升至\s*(\d+)\s*[！!]")


def _normalize_tag(text):
    return str(text or "").strip().lstrip("@").lower()


def _find_small_world_identity_id(text):
    matched = RE_SMALL_WORLD_TARGET_TAG.search(text or "")
    if not matched:
        return None

    target_key = _normalize_tag(matched.group(1))
    if not target_key:
        return None

    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        normalized_tags = {_normalize_tag(tag) for tag in get_send_as_tags(identity_id) if tag}
        if target_key in normalized_tags:
            matched_ids.append(identity_id)
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _has_active_small_world_pending(now):
    reply_to_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    deadline = float(state.get("next_small_world_time", 0) or 0)
    return reply_to_msg_id > 0 and deadline > now


def _clear_small_world_pending():
    state["small_world_preach_reply_to_msg_id"] = 0
    state["next_small_world_time"] = 0


async def _send_small_world_preach(now, reason):
    sent_msg = await send_game_command(CMD_SMALL_WORLD_PREACH, track=False)
    sent_at = float(getattr(sent_msg, "sent_at", 0) or time.time()) if sent_msg else time.time()
    if not sent_msg:
        state["small_world_last_error"] = "神迹布道指令发送失败"
        save_state()
        await send_audit_log("❌ 小世界布道发送失败，稍后重试。")
        return False

    state["small_world_preach_reply_to_msg_id"] = int(getattr(sent_msg, "id", 0) or 0)
    state["next_small_world_time"] = float(sent_at + SMALL_WORLD_PREACH_REPLY_TIMEOUT_SEC)
    state["small_world_last_error"] = ""
    save_state()
    await send_audit_log(f"🌍 小世界{reason}，已发送神迹布道")
    return True


def get_small_world_status_text():
    faith_value = int(state.get("small_world_faith_value", 0) or 0)
    reply_to_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    deadline = float(state.get("next_small_world_time", 0) or 0)
    lines = [
        "🌍 小世界",
        f"- 已启用：{'是' if state.get('small_world_enabled') else '否'}",
        f"- 当前信仰：{faith_value if faith_value > 0 else '未记录'}",
        f"- 待布道消息ID：{reply_to_msg_id or '无'}",
        f"- 等待截止：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）",
        f"- 最近错误：{state.get('small_world_last_error') or '无'}",
    ]
    return "\n".join(lines)


def clear_small_world_state(*, persist=False, keep_last_error=False):
    _clear_small_world_pending()
    state["small_world_faith_value"] = 0
    if not keep_last_error:
        state["small_world_last_error"] = ""
    if persist:
        save_state()
    else:
        mark_dirty()


async def handle_small_world_disaster_broadcast(text, now, event):
    if not state.get("small_world_enabled"):
        return False

    raw_text = text or ""
    if not RE_SMALL_WORLD_DISASTER.search(raw_text) or not RE_SMALL_WORLD_FAITH_DAMAGE.search(raw_text):
        return False

    identity_id = _find_small_world_identity_id(raw_text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False

    if _has_active_small_world_pending(now):
        return True

    return await _send_small_world_preach(now, "监听到信仰异常")


async def handle_small_world_preach_reply(text, now, reply_to, matched_family=None):
    if matched_family and matched_family != "small_world_preach":
        return False
    if not state.get("small_world_enabled"):
        return False

    raw_text = text or ""
    if not RE_SMALL_WORLD_PREACH_PANEL.search(raw_text):
        return False

    matched = RE_SMALL_WORLD_FAITH_VALUE.search(raw_text)
    if not matched:
        state["small_world_last_error"] = "神迹布道回复未解析到信仰值"
        _clear_small_world_pending()
        save_state()
        return True

    faith_value = int(matched.group(1))
    state["small_world_faith_value"] = faith_value
    _clear_small_world_pending()
    state["small_world_last_error"] = ""
    save_state()

    if faith_value >= 100:
        await send_audit_log(f"🌍 小世界信仰已恢复至 {faith_value}")
        return True

    await _send_small_world_preach(now, f"信仰值 {faith_value}<100")
    return True


async def run_small_world_scheduler(now):
    if not state.get("small_world_enabled"):
        return

    reply_to_msg_id = int(state.get("small_world_preach_reply_to_msg_id", 0) or 0)
    deadline = float(state.get("next_small_world_time", 0) or 0)
    if reply_to_msg_id <= 0 or deadline <= 0 or now < deadline:
        return

    state["small_world_last_error"] = "神迹布道回复超时"
    _clear_small_world_pending()
    save_state()
    await send_audit_log(f"⚠️ 小世界神迹布道回复超时，消息ID={reply_to_msg_id}")


__all__ = [
    "clear_small_world_state",
    "get_small_world_status_text",
    "handle_small_world_disaster_broadcast",
    "handle_small_world_preach_reply",
    "run_small_world_scheduler",
]
