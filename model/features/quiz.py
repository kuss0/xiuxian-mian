import json
import re

from ..config import CMD_QUIZ_ANSWER, QUIZ_BANK_FILE, QUIZ_REPLY_TIMEOUT_SEC
from ..persistence import mark_dirty, save_state
from ..runtime import send_audit_log, send_game_command
from ..state import get_current_identity_id, get_identity_enabled, get_identity_ids, get_send_as_tags, state
from ..timing import fmt_abs_ts, fmt_remaining

RE_QUIZ_PROMPT = re.compile(r"你有\s*(\d+)\s*秒")
RE_QUIZ_QUESTION = re.compile(r"[“\"]{1,2}(.+?)[”\"]{1,2}", re.S)
RE_QUIZ_OPTION = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$", re.M)
RE_QUIZ_COMMAND_HINT = re.compile(r"回复本消息并使用\s*\.作答\s*<选项>")
RE_WHITESPACE = re.compile(r"\s+")
RE_PUNCT_ONLY = re.compile(r"[\s\u3000“”‘’""'《》〈〉【】()（）\[\]{}，。！？、；：:,.!?;·…—-]+")

_QUIZ_BANK = None
_QUIZ_BANK_INDEX = None


def _normalize_text(text):
    normalized = RE_WHITESPACE.sub("", text or "")
    return normalized.strip().lower()


def _normalize_relaxed_text(text):
    normalized = _normalize_text(text)
    normalized = RE_PUNCT_ONLY.sub("", normalized)
    return normalized.strip()


def _load_quiz_bank():
    global _QUIZ_BANK, _QUIZ_BANK_INDEX
    if _QUIZ_BANK is not None and _QUIZ_BANK_INDEX is not None:
        return _QUIZ_BANK, _QUIZ_BANK_INDEX

    try:
        with open(QUIZ_BANK_FILE, "r", encoding="utf-8") as f:
            raw_items = json.load(f)
    except Exception:
        raw_items = []

    bank = []
    index = {}
    relaxed_index = {}
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip().upper()
        options = {key: str(item.get(key) or "").strip() for key in ("A", "B", "C", "D")}
        if not question or answer not in {"A", "B", "C", "D"}:
            continue
        normalized_question = _normalize_text(question)
        relaxed_question = _normalize_relaxed_text(question)
        record = {
            "question": question,
            "normalized_question": normalized_question,
            "relaxed_question": relaxed_question,
            "options": options,
            "answer": answer,
        }
        bank.append(record)
        if normalized_question and normalized_question not in index:
            index[normalized_question] = record
        if relaxed_question and relaxed_question not in relaxed_index:
            relaxed_index[relaxed_question] = record

    _QUIZ_BANK = bank
    _QUIZ_BANK_INDEX = {
        "exact": index,
        "relaxed": relaxed_index,
    }
    return _QUIZ_BANK, _QUIZ_BANK_INDEX


def get_quiz_status_text():
    return (
        "🦴 玄骨考校\n"
        f"- 当前题目：{state.get('quiz_question') or '暂无'}\n"
        f"- 匹配答案：{state.get('quiz_answer') or '未匹配'}\n"
        f"- 待回复消息ID：{int(state.get('quiz_reply_to_msg_id', 0) or 0) or '无'}\n"
        f"- 截止时间：{fmt_abs_ts(state.get('next_quiz_time', 0))}（{fmt_remaining(state.get('next_quiz_time', 0))}）\n"
        f"- 最近错误：{state.get('quiz_last_error') or '无'}"
    )


def clear_quiz_state(*, persist=False):
    state["next_quiz_time"] = 0
    state["quiz_reply_to_msg_id"] = 0
    state["quiz_question"] = ""
    state["quiz_options"] = {}
    state["quiz_answer"] = ""
    state["quiz_last_error"] = ""
    state["quiz_last_matched_at"] = 0
    if persist:
        save_state()
    else:
        mark_dirty()


def _find_quiz_identity_id(text):
    compact_text = _normalize_text(text)
    if not compact_text:
        return None
    matched_ids = []
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        tags = get_send_as_tags(identity_id)
        if not tags:
            continue
        normalized_tags = {_normalize_text(tag) for tag in tags if tag}
        if any(tag and tag in compact_text for tag in normalized_tags):
            matched_ids.append(identity_id)
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _parse_quiz_prompt(text):
    raw_text = text or ""
    if "作答" not in raw_text or not RE_QUIZ_COMMAND_HINT.search(raw_text):
        return None
    question_match = RE_QUIZ_QUESTION.search(raw_text)
    options = {key: value.strip() for key, value in RE_QUIZ_OPTION.findall(raw_text)}
    if not question_match or len(options) < 2:
        return None
    question = (question_match.group(1) or "").strip()
    if not question:
        return None
    timeout_sec = QUIZ_REPLY_TIMEOUT_SEC
    timeout_match = RE_QUIZ_PROMPT.search(raw_text)
    if timeout_match:
        try:
            timeout_sec = int(timeout_match.group(1))
        except (TypeError, ValueError):
            timeout_sec = QUIZ_REPLY_TIMEOUT_SEC
    return {
        "question": question,
        "options": options,
        "timeout_sec": max(1, timeout_sec),
    }


def _match_quiz_answer(question, options):
    bank, index_bundle = _load_quiz_bank()
    exact_index = index_bundle.get("exact", {})
    relaxed_index = index_bundle.get("relaxed", {})
    normalized_question = _normalize_text(question)
    relaxed_question = _normalize_relaxed_text(question)
    if not normalized_question:
        return "", ""

    direct_match = exact_index.get(normalized_question)
    if direct_match:
        answer = direct_match["answer"]
        expected_option = _normalize_text(direct_match["options"].get(answer, ""))
        current_option = _normalize_text((options or {}).get(answer, ""))
        if not expected_option or not current_option or expected_option == current_option:
            return answer, "exact_question"

    normalized_current_options = {key: _normalize_text(value) for key, value in (options or {}).items()}
    for item in bank:
        if item["normalized_question"] != normalized_question:
            continue
        expected_answer = item["answer"]
        expected_text = _normalize_text(item["options"].get(expected_answer, ""))
        if not expected_text:
            continue
        for key, value in normalized_current_options.items():
            if value and value == expected_text:
                return key, "exact_option_text"

    relaxed_match = relaxed_index.get(relaxed_question)
    if relaxed_match:
        answer = relaxed_match["answer"]
        expected_option = _normalize_relaxed_text(relaxed_match["options"].get(answer, ""))
        current_option = _normalize_relaxed_text((options or {}).get(answer, ""))
        if not expected_option or not current_option or expected_option == current_option:
            return answer, "relaxed_question"

    relaxed_current_options = {key: _normalize_relaxed_text(value) for key, value in (options or {}).items()}
    for item in bank:
        if item.get("relaxed_question") != relaxed_question:
            continue
        expected_answer = item["answer"]
        expected_text = _normalize_relaxed_text(item["options"].get(expected_answer, ""))
        if not expected_text:
            continue
        for key, value in relaxed_current_options.items():
            if value and value == expected_text:
                return key, "relaxed_option_text"

    return "", ""


async def handle_quiz_prompt(text, now, event):
    if not state.get("quiz_enabled"):
        return False
    if int(state.get("quiz_reply_to_msg_id", 0) or 0) > 0 and float(state.get("next_quiz_time", 0) or 0) > now:
        return False

    parsed = _parse_quiz_prompt(text)
    if not parsed:
        return False

    identity_id = _find_quiz_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return False

    answer, match_mode = _match_quiz_answer(parsed["question"], parsed["options"])
    if not answer:
        state["quiz_question"] = parsed["question"]
        state["quiz_options"] = dict(parsed["options"])
        state["quiz_answer"] = ""
        state["quiz_reply_to_msg_id"] = int(getattr(event, "id", 0) or 0)
        state["next_quiz_time"] = now + float(parsed["timeout_sec"])
        state["quiz_last_error"] = "题库未命中"
        state["quiz_last_matched_at"] = 0
        save_state()
        options_text = " | ".join(
            f"{key}.{value}" for key, value in sorted(parsed["options"].items()) if value
        )
        await send_audit_log(
            f"🦴 玄骨考校题库未命中[{parsed['question']}]，选项：{options_text}"
        )
        return True

    state["quiz_question"] = parsed["question"]
    state["quiz_options"] = dict(parsed["options"])
    state["quiz_answer"] = answer
    state["quiz_reply_to_msg_id"] = int(getattr(event, "id", 0) or 0)
    state["next_quiz_time"] = now + float(parsed["timeout_sec"])
    state["quiz_last_error"] = ""
    state["quiz_last_matched_at"] = now
    save_state()

    reply_msg = await send_game_command(f"{CMD_QUIZ_ANSWER} {answer}", track=False, reply_to=state["quiz_reply_to_msg_id"])
    if not reply_msg:
        state["quiz_last_error"] = "作答发送失败"
        save_state()
        await send_audit_log(f"❌ 玄骨考校作答发送失败，题目：{parsed['question']}")
        return True

    await send_audit_log(
        f"🦴 已自动作答玄骨考校，答案：{answer}，匹配方式：{match_mode}，题目：{parsed['question']}"
    )
    clear_quiz_state(persist=True)
    return True


async def run_quiz_scheduler(now):
    if not state.get("quiz_enabled"):
        return
    next_quiz_time = float(state.get("next_quiz_time", 0) or 0)
    if next_quiz_time <= 0 or now < next_quiz_time:
        return
    question = state.get("quiz_question") or "未记录题目"
    state["quiz_last_error"] = "题目已超时"
    save_state()
    await send_audit_log(f"⚠️ 玄骨考校题目已超时，未继续处理：{question}")
    clear_quiz_state(persist=True)


__all__ = [
    "clear_quiz_state",
    "get_quiz_status_text",
    "handle_quiz_prompt",
    "run_quiz_scheduler",
]
