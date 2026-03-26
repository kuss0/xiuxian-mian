import json
import re

from ..config import CMD_QUIZ_ANSWER, QUIZ_BANK_FILE, QUIZ_REPLY_TIMEOUT_SEC
from ..persistence import mark_dirty, save_state
from ..runtime import console_log, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    get_quiz_learning_watchers,
    get_send_as_tags,
    set_quiz_learning_watchers,
    state,
)
from ..timing import fmt_abs_ts, fmt_remaining

RE_QUIZ_PROMPT = re.compile(r"你有\s*(\d+)\s*秒")
RE_QUIZ_QUESTION = re.compile(r"[“\"]{1,2}(.+?)[”\"]{1,2}", re.S)
RE_QUIZ_OPTION = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$", re.M)
RE_QUIZ_COMMAND_HINT = re.compile(r"回复本消息并使用\s*\.作答\s*<选项>")
RE_QUIZ_TARGET_TAG = re.compile(r"@([^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+)")
RE_QUIZ_RESULT_CORRECT = re.compile(
    r"【考校结束[·・•]正确】[\s\S]*?@(?P<tag>[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+)\s*的答案\s*(?P<answer>[A-D])\s*完全正确",
    re.S,
)
RE_QUIZ_RESULT_WRONG = re.compile(
    r"【考校结束[·・•]错误】[\s\S]*?@(?P<tag>[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+)\s*的答案\s*(?P<submitted>[A-D])\s*错[^（(]*[（(]\s*正确答案\s*[:：]\s*(?P<answer>[A-D])\s*[)）]",
    re.S,
)
RE_WHITESPACE = re.compile(r"\s+")
RE_PUNCT_ONLY = re.compile(r"[\s\u3000“”‘’""'《》〈〉【】()（）\[\]{}，。！？、；：:,.!?;·…—-]+")
QUIZ_RESULT_GRACE_SEC = 120

_QUIZ_BANK = None
_QUIZ_BANK_INDEX = None


def _normalize_text(text):
    normalized = RE_WHITESPACE.sub("", text or "")
    return normalized.strip().lower()


def _normalize_relaxed_text(text):
    normalized = _normalize_text(text)
    normalized = RE_PUNCT_ONLY.sub("", normalized)
    return normalized.strip()


def _normalize_quiz_target_key(tag):
    return _normalize_text((tag or "").strip().lstrip("@"))


def _format_quiz_options(options):
    return " | ".join(
        f"{key}.{value}" for key, value in sorted((options or {}).items()) if str(value or "").strip()
    )


def _reset_quiz_bank_cache():
    global _QUIZ_BANK, _QUIZ_BANK_INDEX
    _QUIZ_BANK = None
    _QUIZ_BANK_INDEX = None


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


def _load_quiz_bank_items():
    try:
        with open(QUIZ_BANK_FILE, "r", encoding="utf-8") as f:
            raw_items = json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        return None
    return raw_items if isinstance(raw_items, list) else []


def _write_quiz_bank_items(items):
    with open(QUIZ_BANK_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write("\n")
    _reset_quiz_bank_cache()


def _build_quiz_bank_record(question, options, answer):
    normalized_question = str(question or "").strip()
    normalized_answer = str(answer or "").strip().upper()
    normalized_options = {
        key: str((options or {}).get(key) or "").strip()
        for key in ("A", "B", "C", "D")
    }
    if not normalized_question or normalized_answer not in {"A", "B", "C", "D"}:
        return None
    if any(not normalized_options.get(key) for key in ("A", "B", "C", "D")):
        return None
    return {
        "question": normalized_question,
        "A": normalized_options["A"],
        "B": normalized_options["B"],
        "C": normalized_options["C"],
        "D": normalized_options["D"],
        "answer": normalized_answer,
    }


def _classify_quiz_bank_record(existing_items, new_record):
    question = str(new_record.get("question") or "").strip()
    answer = str(new_record.get("answer") or "").strip().upper()
    options = {key: str(new_record.get(key) or "").strip() for key in ("A", "B", "C", "D")}
    question_key = _normalize_text(question)
    relaxed_question_key = _normalize_relaxed_text(question)
    correct_text = _normalize_text(options.get(answer, ""))
    relaxed_correct_text = _normalize_relaxed_text(options.get(answer, ""))
    if not question_key or not correct_text:
        return "invalid", None

    for item in existing_items:
        if not isinstance(item, dict):
            continue
        existing_question = str(item.get("question") or "").strip()
        existing_answer = str(item.get("answer") or "").strip().upper()
        existing_options = {key: str(item.get(key) or "").strip() for key in ("A", "B", "C", "D")}
        if not existing_question or existing_answer not in {"A", "B", "C", "D"}:
            continue
        existing_question_key = _normalize_text(existing_question)
        existing_relaxed_question_key = _normalize_relaxed_text(existing_question)
        same_question = (
            (existing_question_key and existing_question_key == question_key)
            or (relaxed_question_key and existing_relaxed_question_key == relaxed_question_key)
        )
        if not same_question:
            continue
        existing_correct_text = _normalize_text(existing_options.get(existing_answer, ""))
        existing_relaxed_correct_text = _normalize_relaxed_text(existing_options.get(existing_answer, ""))
        same_correct_option = (
            (existing_correct_text and existing_correct_text == correct_text)
            or (relaxed_correct_text and existing_relaxed_correct_text == relaxed_correct_text)
        )
        if same_correct_option:
            return "exists", item
        return "conflict", item
    return "new", None


def _save_quiz_bank_entry(question, options, answer):
    record = _build_quiz_bank_record(question, options, answer)
    if not record:
        return "invalid", None
    items = _load_quiz_bank_items()
    if items is None:
        return "io_error", None
    status, existing = _classify_quiz_bank_record(items, record)
    if status != "new":
        return status, existing
    try:
        items.append(record)
        _write_quiz_bank_items(items)
    except Exception:
        return "io_error", None
    return "added", record


def _extract_quiz_target_tag(text):
    matched_tags = {}
    for raw_tag in RE_QUIZ_TARGET_TAG.findall(text or ""):
        target_tag = str(raw_tag or "").strip()
        watcher_key = _normalize_quiz_target_key(target_tag)
        if watcher_key and watcher_key not in matched_tags:
            matched_tags[watcher_key] = f"@{target_tag}"
    if len(matched_tags) != 1:
        return "", ""
    watcher_key, target_tag = next(iter(matched_tags.items()))
    return target_tag, watcher_key


def _parse_quiz_result(text):
    raw_text = text or ""
    correct_match = RE_QUIZ_RESULT_CORRECT.search(raw_text)
    if correct_match:
        target_tag = f"@{str(correct_match.group('tag') or '').strip()}"
        watcher_key = _normalize_quiz_target_key(target_tag)
        answer = str(correct_match.group("answer") or "").strip().upper()
        if watcher_key and answer in {"A", "B", "C", "D"}:
            return {
                "status": "correct",
                "target_tag": target_tag,
                "target_key": watcher_key,
                "submitted_answer": answer,
                "correct_answer": answer,
            }

    wrong_match = RE_QUIZ_RESULT_WRONG.search(raw_text)
    if wrong_match:
        target_tag = f"@{str(wrong_match.group('tag') or '').strip()}"
        watcher_key = _normalize_quiz_target_key(target_tag)
        submitted_answer = str(wrong_match.group("submitted") or "").strip().upper()
        correct_answer = str(wrong_match.group("answer") or "").strip().upper()
        if watcher_key and correct_answer in {"A", "B", "C", "D"}:
            return {
                "status": "wrong",
                "target_tag": target_tag,
                "target_key": watcher_key,
                "submitted_answer": submitted_answer if submitted_answer in {"A", "B", "C", "D"} else "",
                "correct_answer": correct_answer,
            }
    return None


def _set_quiz_learning_watcher(target_key, watcher, *, persist=False):
    watchers = dict(get_quiz_learning_watchers())
    watchers[str(target_key or "").strip().lower()] = dict(watcher or {})
    set_quiz_learning_watchers(watchers)
    if persist:
        save_state()
    else:
        mark_dirty()


def _pop_quiz_learning_watcher(target_key, *, persist=False):
    watchers = dict(get_quiz_learning_watchers())
    removed = watchers.pop(str(target_key or "").strip().lower(), None)
    if removed is None:
        return None
    set_quiz_learning_watchers(watchers)
    if persist:
        save_state()
    else:
        mark_dirty()
    return removed


def _get_quiz_learning_watcher(target_key):
    watchers = get_quiz_learning_watchers()
    return watchers.get(str(target_key or "").strip().lower())


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


async def handle_quiz_learning_prompt(text, now, event):
    parsed = _parse_quiz_prompt(text)
    if not parsed:
        return False

    target_tag, target_key = _extract_quiz_target_tag(text)
    if not target_key:
        return False

    matched_answer, _ = _match_quiz_answer(parsed["question"], parsed["options"])
    watcher = {
        "target_tag": target_tag,
        "question": parsed["question"],
        "options": dict(parsed["options"]),
        "prompt_msg_id": int(getattr(event, "id", 0) or 0),
        "prompt_ts": float(now),
        "expire_at": float(now + float(parsed["timeout_sec"]) + QUIZ_RESULT_GRACE_SEC),
        "matched_answer": matched_answer,
    }
    _set_quiz_learning_watcher(target_key, watcher, persist=True)
    return True


async def handle_quiz_result_broadcast(text, now):
    parsed = _parse_quiz_result(text)
    if not parsed:
        return False

    watcher = _get_quiz_learning_watcher(parsed["target_key"])
    if not watcher:
        return False

    question = str(watcher.get("question") or "").strip()
    options = dict(watcher.get("options") or {})
    correct_answer = str(parsed.get("correct_answer") or "").strip().upper()
    matched_answer = str(watcher.get("matched_answer") or "").strip().upper()
    target_tag = watcher.get("target_tag") or parsed.get("target_tag") or "未知目标"
    correct_option_text = str(options.get(correct_answer) or "").strip()

    if matched_answer and matched_answer != correct_answer:
        _pop_quiz_learning_watcher(parsed["target_key"], persist=True)
        await send_audit_log(
            "🦴 玄骨考校题库冲突，请人工处理\n"
            f"- 目标: {target_tag}\n"
            f"- 题目: {question or '未记录题目'}\n"
            f"- 题库命中: {matched_answer}.{options.get(matched_answer) or '未知'}\n"
            f"- 结算答案: {correct_answer}.{correct_option_text or '未知'}\n"
            f"- 选项: {_format_quiz_options(options)}"
        )
        return True

    status, payload = _save_quiz_bank_entry(question, options, correct_answer)
    if status == "added":
        _pop_quiz_learning_watcher(parsed["target_key"], persist=True)
        await send_audit_log(
            f"🦴 已学习玄骨考校新题[{target_tag}]，答案：{correct_answer}，题目：{question}"
        )
        return True

    if status == "exists":
        _pop_quiz_learning_watcher(parsed["target_key"], persist=True)
        return True

    if status == "conflict":
        existing_answer = str((payload or {}).get("answer") or "").strip().upper()
        existing_option_text = str((payload or {}).get(existing_answer) or "").strip()
        _pop_quiz_learning_watcher(parsed["target_key"], persist=True)
        await send_audit_log(
            "🦴 玄骨考校题库冲突，请人工处理\n"
            f"- 目标: {target_tag}\n"
            f"- 题目: {question or '未记录题目'}\n"
            f"- 题库记录: {existing_answer}.{existing_option_text or '未知'}\n"
            f"- 结算答案: {correct_answer}.{correct_option_text or '未知'}\n"
            f"- 选项: {_format_quiz_options(options)}"
        )
        return True

    if status == "invalid":
        _pop_quiz_learning_watcher(parsed["target_key"], persist=True)
        await send_audit_log(
            "🦴 玄骨考校学习失败，题目数据不完整\n"
            f"- 目标: {target_tag}\n"
            f"- 题目: {question or '未记录题目'}\n"
            f"- 正确答案: {correct_answer}\n"
            f"- 选项: {_format_quiz_options(options)}"
        )
        return True

    if status == "io_error":
        await send_audit_log(
            "🦴 玄骨考校题库写入失败\n"
            f"- 目标: {target_tag}\n"
            f"- 题目: {question or '未记录题目'}\n"
            f"- 正确答案: {correct_answer}\n"
            f"- 选项: {_format_quiz_options(options)}"
        )
        return True

    return False


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
        await send_audit_log(
            f"🦴 玄骨考校题库未命中[{parsed['question']}]，选项：{_format_quiz_options(parsed['options'])}"
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


async def run_quiz_learning_scheduler(now):
    watchers = dict(get_quiz_learning_watchers())
    if not watchers:
        return
    expired_keys = [
        watcher_key
        for watcher_key, watcher in watchers.items()
        if float((watcher or {}).get("expire_at", 0) or 0) > 0
        and now >= float((watcher or {}).get("expire_at", 0) or 0)
    ]
    if not expired_keys:
        return
    for watcher_key in expired_keys:
        watchers.pop(watcher_key, None)
    set_quiz_learning_watchers(watchers)
    mark_dirty()


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
    "handle_quiz_learning_prompt",
    "handle_quiz_prompt",
    "handle_quiz_result_broadcast",
    "run_quiz_learning_scheduler",
    "run_quiz_scheduler",
]
