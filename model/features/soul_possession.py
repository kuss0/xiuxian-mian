"""Pause only an owned, trusted soul-loss reply; ordinary italic prose is harmless."""
import re

from ..persistence import mark_dirty
from ..runtime import send_audit_log
from ..state import get_expedition_run_state, get_game_bot_ids, has_identity


def is_soul_weak_reply_text(text):
    text = str(text or "").strip()
    return (len(text) > 2 and text[0] in "*＊" and text[-1] in "*＊"
            and bool(re.search(r"残魂|婴身|元婴.{0,12}虚弱|夺舍|把肉身给我|肉身.{0,8}毁", text)))


def clear_soul_possession_pause(identity_id):
    pauses = get_expedition_run_state().get("soul_pauses", {})
    if pauses.pop(str(identity_id), None) is not None:
        mark_dirty()
        return True
    return False


async def handle_soul_weak_reply_event(event, text, now, *, identity_id, root_msg_id):
    if (not root_msg_id or not has_identity(identity_id) or not is_soul_weak_reply_text(text)
            or getattr(event, "sender_id", 0) not in get_game_bot_ids()
            or getattr(event, "forwarded", False) or getattr(event, "fwd_from", None)):
        return False
    pauses = get_expedition_run_state().setdefault("soul_pauses", {})
    if str(identity_id) not in pauses:
        pauses[str(identity_id)] = {"at": now, "root_msg_id": root_msg_id, "reason": text[:180]}
        mark_dirty()
        await send_audit_log("检测到残魂旁白，已暂停该身份普通指令；夺舍成功后恢复。",
                             scope="identity", send_as_id=identity_id, limit=240)
    return True
