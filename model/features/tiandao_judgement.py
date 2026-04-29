import random
import re

from ..config import CMD_TIANDAO_JUDGEMENT_PROVE
from ..persistence import save_state
from ..runtime import console_log, mono, send_audit_log, send_game_command
from ..state import get_identity_ids, get_send_as_profile, state
from ..timing import fmt_time_after


TIANDAO_JUDGEMENT_PROMPT_KEYWORDS = ("天道审判", "天道问心", "自证")
TIANDAO_JUDGEMENT_RETRY_DELAY_SEC = 10
TIANDAO_JUDGEMENT_DEADLINE_BUFFER_SEC = 5
TIANDAO_JUDGEMENT_DELAY_MIN_SEC = 40
TIANDAO_JUDGEMENT_DELAY_MAX_SEC = 60
TIANDAO_JUDGEMENT_DEFAULT_TIMEOUT_SEC = 3 * 60

TIANDAO_JUDGEMENT_VALUE_MAP = {
    "炼制玄铁剑消耗灵石": 10,
    "结丹期点卯贡献": 50,
    "三才微尘阵消耗灵石": 1000,
    "天道筑基丹官方售价": 550,
    "小药园初始灵田数量": 3,
    "炼制增元丹需凝血草": 4,
    "炼气一层所需修为": 100,
    "结丹初期所需修为": 50000,
    "开辟洞府消耗灵石": 500,
    "引星盘初始座数": 3,
    "元婴初期所需修为": 500000,
    "元婴期点卯贡献": 100,
    "洗髓丹所需LDC积分": 10,
    "叛出宗门冷却小时": 4,
    "宗门传功所得贡献": 30,
    "筑基初期所需修为": 5000,
    "筑基期点卯贡献": 15,
    "元婴出窍历练时长": 8,
    "炼制金光砖需金精矿": 12,
    "大庚剑阵消耗修为": 2000,
}

RE_TIANDAO_TARGET = re.compile(r"对象\s*[【\[]\s*([^】\]]+?)\s*[】\]]")
RE_TIANDAO_TIMEOUT_MIN = re.compile(r"(\d+)\s*分钟")
RE_TIANDAO_TIMEOUT_SEC = re.compile(r"(\d+)\s*秒")
RE_TIANDAO_QUESTION = re.compile(
    r"天道问心\s*[:：]\s*(?P<left>.+?)\s*(?P<op>加|减|乘|除)\s*"
    r"(?P<right>[零〇一二两三四五六七八九十百千万萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟\d０-９]+)\s*等于\s*[?？]",
    re.S,
)
RE_IDENTITY_SEPARATORS = re.compile(r"[\s@，。！？、；：:,.!?\[\]【】()（）<>《》“”\"'`]+")
RE_QUESTION_WHITESPACE = re.compile(r"\s+")

CHINESE_DIGIT_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "壹": 1,
    "二": 2,
    "贰": 2,
    "貳": 2,
    "两": 2,
    "三": 3,
    "叁": 3,
    "參": 3,
    "四": 4,
    "肆": 4,
    "五": 5,
    "伍": 5,
    "六": 6,
    "陆": 6,
    "陸": 6,
    "七": 7,
    "柒": 7,
    "八": 8,
    "捌": 8,
    "九": 9,
    "玖": 9,
}
CHINESE_UNIT_VALUES = {
    "十": 10,
    "拾": 10,
    "百": 100,
    "佰": 100,
    "千": 1000,
    "仟": 1000,
}
FULL_WIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _is_tiandao_judgement_prompt(text):
    raw_text = str(text or "")
    return all(keyword in raw_text for keyword in TIANDAO_JUDGEMENT_PROMPT_KEYWORDS)


def _normalize_question_left(text):
    return RE_QUESTION_WHITESPACE.sub("", str(text or "")).strip("✨:： ")


def _normalize_identity_text(text):
    return RE_IDENTITY_SEPARATORS.sub("", str(text or "").strip().lstrip("@")).lower()


def _parse_chinese_integer(text):
    normalized = str(text or "").strip().translate(FULL_WIDTH_DIGITS)
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)

    total = 0
    section = 0
    number = 0
    for char in normalized:
        if char in CHINESE_DIGIT_VALUES:
            number = CHINESE_DIGIT_VALUES[char]
        elif char in CHINESE_UNIT_VALUES:
            unit_value = CHINESE_UNIT_VALUES[char]
            section += (number or 1) * unit_value
            number = 0
        elif char in {"万", "萬"}:
            section += number
            total += (section or 1) * 10000
            section = 0
            number = 0
        else:
            return None
    return total + section + number


def _parse_timeout_sec(text):
    raw_text = str(text or "")
    minute_match = RE_TIANDAO_TIMEOUT_MIN.search(raw_text)
    if minute_match:
        return max(1, int(minute_match.group(1)) * 60)
    second_match = RE_TIANDAO_TIMEOUT_SEC.search(raw_text)
    if second_match:
        return max(1, int(second_match.group(1)))
    return TIANDAO_JUDGEMENT_DEFAULT_TIMEOUT_SEC


def _format_answer(value):
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _calculate_answer(left_value, op, right_value):
    if op == "加":
        return _format_answer(left_value + right_value)
    if op == "减":
        return _format_answer(left_value - right_value)
    if op == "乘":
        return _format_answer(left_value * right_value)
    if op == "除":
        if right_value == 0:
            return ""
        if left_value % right_value == 0:
            return _format_answer(left_value // right_value)
        return _format_answer(left_value / right_value)
    return ""


def _extract_tiandao_judgement_question(text):
    if not _is_tiandao_judgement_prompt(text):
        return None

    raw_text = str(text or "")
    question_match = RE_TIANDAO_QUESTION.search(raw_text)
    if not question_match:
        return None

    left_text = _normalize_question_left(question_match.group("left"))
    right_text = str(question_match.group("right") or "").strip()
    op = str(question_match.group("op") or "").strip()
    target_match = RE_TIANDAO_TARGET.search(raw_text)
    target = str(target_match.group(1) or "").strip() if target_match else ""
    return {
        "target": target,
        "question": f"{left_text}{op}{right_text}",
        "left_text": left_text,
        "op": op,
        "right_text": right_text,
        "timeout_sec": _parse_timeout_sec(raw_text),
    }


def parse_tiandao_judgement_prompt(text):
    parsed = _extract_tiandao_judgement_question(text)
    if not parsed:
        return None

    left_value = TIANDAO_JUDGEMENT_VALUE_MAP.get(parsed["left_text"])
    right_value = _parse_chinese_integer(parsed["right_text"])
    if left_value is None or right_value is None:
        return None

    answer = _calculate_answer(left_value, parsed["op"], right_value)
    if not answer:
        return None

    parsed.update({
        "left_value": left_value,
        "right_value": right_value,
        "answer": answer,
    })
    return parsed


def _get_pending_map():
    pending = state.get("tiandao_judgement_pending", {})
    return dict(pending) if isinstance(pending, dict) else {}


def _set_pending_map(pending):
    state["tiandao_judgement_pending"] = dict(pending or {})
    save_state()


def _get_event_pending_key(event, parsed):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if msg_id > 0 and chat_id:
        return f"{chat_id}:{msg_id}"
    return f"{parsed.get('target') or 'unknown'}:{parsed.get('question') or ''}:{parsed.get('answer') or ''}"


def _find_target_identity_id(target):
    target_key = _normalize_identity_text(target)
    if not target_key:
        return None

    matched_ids = []
    for identity_id in get_identity_ids():
        profile = get_send_as_profile(identity_id)
        candidates = {
            str(identity_id),
            profile.get("username", ""),
            profile.get("label", ""),
            profile.get("daohao", ""),
        }
        if any(_normalize_identity_text(candidate) == target_key for candidate in candidates if candidate):
            matched_ids.append(int(identity_id))
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


async def _send_tiandao_judgement_parse_failure_log(text):
    question = _extract_tiandao_judgement_question(text)
    if question:
        left_text = question.get("left_text") or ""
        question_text = question.get("question") or ""
        if left_text and left_text not in TIANDAO_JUDGEMENT_VALUE_MAP:
            await send_audit_log(f"⚖️ 天道审判题目未匹配：{mono(question_text)}", scope="global", limit=360)
            return
        await send_audit_log(f"⚖️ 天道审判解析失败：{mono(question_text or '未知题目')}", scope="global", limit=360)
        return
    await send_audit_log("⚖️ 天道审判解析失败，请手动处理。", scope="global")


def _build_pending_item(parsed, identity_id, event, now):
    delay_sec = random.uniform(TIANDAO_JUDGEMENT_DELAY_MIN_SEC, TIANDAO_JUDGEMENT_DELAY_MAX_SEC)
    timeout_sec = float(parsed.get("timeout_sec") or TIANDAO_JUDGEMENT_DEFAULT_TIMEOUT_SEC)
    deadline_at = float(now) + timeout_sec
    due_at = float(now) + delay_sec
    if due_at >= deadline_at:
        due_at = max(float(now) + 1, deadline_at - TIANDAO_JUDGEMENT_DEADLINE_BUFFER_SEC)
    return {
        "target": parsed.get("target") or "",
        "identity_id": int(identity_id),
        "question": parsed.get("question") or "",
        "answer": parsed.get("answer") or "",
        "due_at": float(due_at),
        "deadline_at": float(deadline_at),
        "created_at": float(now),
        "msg_id": int(getattr(event, "id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "retry_count": 0,
    }


async def handle_tiandao_judgement_prompt(text, now, event=None):
    if not state.get("tiandao_judgement_enabled"):
        return False
    if not _is_tiandao_judgement_prompt(text):
        return False

    parsed = parse_tiandao_judgement_prompt(text)
    if not parsed:
        await _send_tiandao_judgement_parse_failure_log(text)
        return True

    identity_id = _find_target_identity_id(parsed.get("target"))
    if identity_id is None:
        await send_audit_log(f"⚖️ 天道审判未匹配身份：{mono(parsed.get('target') or '未知对象')}", scope="global", limit=260)
        return True

    pending_key = _get_event_pending_key(event, parsed)
    pending = _get_pending_map()
    if pending_key in pending:
        return True

    item = _build_pending_item(parsed, identity_id, event, now)
    pending[pending_key] = item
    _set_pending_map(pending)
    console_log(
        f"⚖️ 天道审判排队：{parsed['target']}｜答案 {parsed['answer']}｜{fmt_time_after(item['due_at'] - now)}后",
        scope="global",
    )
    return True


async def run_tiandao_judgement_scheduler(now):
    if not state.get("tiandao_judgement_enabled"):
        return

    pending = _get_pending_map()
    if not pending:
        return

    changed = False
    for pending_key, item in list(pending.items()):
        identity_id = int((item or {}).get("identity_id", 0) or 0)
        target = str((item or {}).get("target") or "未知对象")
        answer = str((item or {}).get("answer") or "").strip()
        due_at = float((item or {}).get("due_at", 0) or 0)
        deadline_at = float((item or {}).get("deadline_at", 0) or 0)

        if not answer or (deadline_at > 0 and now >= deadline_at):
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"⚖️ 天道审判已超时：{mono(target)}", scope="global", limit=260)
            continue
        if due_at <= 0 or now < due_at:
            continue
        if identity_id <= 0 or identity_id not in get_identity_ids():
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"⚖️ 天道审判未发送：{mono(target)} 身份不存在", scope="global", limit=260)
            continue

        msg = await send_game_command(f"{CMD_TIANDAO_JUDGEMENT_PROVE} {answer}", track=False, send_as_id=identity_id)
        if msg:
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"⚖️ 天道审判自证：{mono(target)}｜{answer}", scope="global", limit=260)
            continue

        retry_count = int((item or {}).get("retry_count", 0) or 0) + 1
        item["retry_count"] = retry_count
        item["due_at"] = min(now + TIANDAO_JUDGEMENT_RETRY_DELAY_SEC, max(now + 1, deadline_at - 1)) if deadline_at > now + 1 else now + 1
        pending[pending_key] = item
        changed = True
        if retry_count == 1:
            await send_audit_log(f"⚖️ 天道审判自证发送失败，稍后重试：{mono(target)}", scope="global", limit=260)

    if changed:
        _set_pending_map(pending)


__all__ = [
    "handle_tiandao_judgement_prompt",
    "parse_tiandao_judgement_prompt",
    "run_tiandao_judgement_scheduler",
]
