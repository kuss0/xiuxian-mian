import json
import re

from ..config import CMD_QUIZ_ANSWER, QUIZ_BANK_FILE, QUIZ_REPLY_TIMEOUT_SEC, RE_WHITESPACE
from ..persistence import mark_dirty, save_quiz_learning_watchers_state, save_state
from ..runtime import mono, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_enabled,
    get_identity_ids,
    get_quiz_learning_watchers,
    get_send_as_tags,
    set_quiz_learning_watchers,
    state,
    use_identity,
)
from ..timing import fmt_abs_ts, fmt_remaining

RE_QUIZ_PROMPT = re.compile(r"你有\s*(\d+)\s*秒")
RE_QUIZ_QUESTION = re.compile(r'["“”]{1,2}(.+?)["“”]{1,2}\s*$', re.M)
RE_QUIZ_OPTION = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$", re.M)
RE_QUIZ_COMMAND_HINT = re.compile(r"回复本消息并使用\s*\.作答\s*<选项>")
QUIZ_TARGET_TAG_PATTERN = r"[^\s@，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+"
RE_QUIZ_TARGET_TAG = re.compile(rf"@({QUIZ_TARGET_TAG_PATTERN})")
RE_QUIZ_RESULT_CORRECT = re.compile(
    rf"【考校结束[·・•]正确】[\s\S]*?@(?P<tag>{QUIZ_TARGET_TAG_PATTERN})\s*的答案\s*(?P<answer>[A-D])\s*完全正确",
    re.S,
)
RE_QUIZ_RESULT_WRONG = re.compile(
    rf"【考校结束[·・•]错误】[\s\S]*?@(?P<tag>{QUIZ_TARGET_TAG_PATTERN})\s*的答案\s*(?P<submitted>[A-D])\s*错[^（(]*[（(]\s*正确答案\s*[:：]\s*(?P<answer>[A-D])\s*[)）]",
    re.S,
)
RE_QUIZ_RESULT_TIMEOUT = re.compile(
    rf"【考校结束[·・•]超时】[\s\S]*?@(?P<tag>{QUIZ_TARGET_TAG_PATTERN})",
    re.S,
)
RE_PUNCT_ONLY = re.compile(r"[][\s\u3000\u201c\u201d\u2018\u2019'《》〈〉【】()（）{}，。！？、；：:,.!?;·…—-]+")
QUIZ_RESULT_GRACE_SEC = 120
QUIZ_ANSWER_CONFIRM_TIMEOUT_SEC = 60
QUIZ_ANSWER_MAX_RETRY_COUNT = 3

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


def _normalize_quiz_identity_id(identity_id):
    try:
        return int(identity_id or 0) or None
    except (TypeError, ValueError):
        return None


def _get_quiz_log_kwargs(identity_id=None, *, limit=220):
    normalized_identity_id = _normalize_quiz_identity_id(identity_id)
    if normalized_identity_id is None:
        return {
            "scope": "global",
            "limit": limit,
        }
    return {
        "scope": "identity",
        "send_as_id": normalized_identity_id,
        "limit": limit,
    }


def _format_quiz_brief_log(text, *, identity_id=None, target_tag=""):
    if _normalize_quiz_identity_id(identity_id) is not None or not target_tag:
        return f"🦴 {text}"
    return f"🦴 {mono(target_tag)}｜{text}"


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
    watcher_key = _normalize_quiz_target_key(target_key)
    if not watcher_key:
        return
    watchers[watcher_key] = dict(watcher or {})
    set_quiz_learning_watchers(watchers)
    if persist:
        save_quiz_learning_watchers_state()
    else:
        mark_dirty()


def _pop_quiz_learning_watcher(target_key, *, persist=False):
    watchers = dict(get_quiz_learning_watchers())
    watcher_key = _normalize_quiz_target_key(target_key)
    if not watcher_key:
        return None
    removed = watchers.pop(watcher_key, None)
    if removed is None:
        return None
    set_quiz_learning_watchers(watchers)
    if persist:
        save_quiz_learning_watchers_state()
    else:
        mark_dirty()
    return removed


def _get_quiz_learning_watcher(target_key):
    watchers = get_quiz_learning_watchers()
    watcher_key = _normalize_quiz_target_key(target_key)
    if not watcher_key:
        return None
    return watchers.get(watcher_key)


def _get_quiz_pending_state():
    return (
        int(state.get("quiz_reply_to_msg_id", 0) or 0),
        float(state.get("next_quiz_time", 0) or 0),
    )


def _get_quiz_retry_count():
    try:
        return int(state.get("quiz_retry_count", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _has_active_quiz_pending(now):
    reply_to_msg_id, deadline = _get_quiz_pending_state()
    return reply_to_msg_id > 0 and deadline > now


def _match_quiz_prompt_for_current_identity(text):
    parsed = _parse_quiz_prompt(text)
    if not parsed:
        return None, None
    identity_id = _find_quiz_identity_id(text)
    if identity_id is None or identity_id != get_current_identity_id():
        return None, None
    return parsed, identity_id


def _set_quiz_pending(question, options, answer, reply_to_msg_id, deadline_at, *, last_error, last_matched_at, retry_count=0, match_mode=""):
    state["quiz_question"] = question
    state["quiz_options"] = dict(options or {})
    state["quiz_answer"] = answer
    state["quiz_reply_to_msg_id"] = int(reply_to_msg_id or 0)
    state["next_quiz_time"] = float(deadline_at or 0)
    state["quiz_retry_count"] = int(retry_count or 0)
    state["quiz_match_mode"] = str(match_mode or "")
    state["quiz_last_error"] = last_error
    state["quiz_last_matched_at"] = last_matched_at


def _set_quiz_error_and_save(message):
    state["quiz_last_error"] = message
    save_state()


async def _send_quiz_answer(answer, reply_to_msg_id):
    return await send_game_command(f"{CMD_QUIZ_ANSWER} {answer}", track=False, reply_to=reply_to_msg_id)


async def _finalize_quiz_success(audit_text, *, identity_id):
    await send_audit_log(
        audit_text,
        scope="identity",
        send_as_id=identity_id,
        limit=360,
    )
    clear_quiz_state(persist=True)


def _get_quiz_result_identity_id(parsed, watcher):
    identity_id = _normalize_quiz_identity_id((watcher or {}).get("identity_id"))
    if identity_id is not None:
        return identity_id
    return _find_quiz_identity_id((parsed or {}).get("target_tag") or "", enabled_only=False)


def _quiz_pending_matches_result(parsed, watcher):
    if int(state.get("quiz_reply_to_msg_id", 0) or 0) <= 0:
        return False
    pending_answer = str(state.get("quiz_answer") or "").strip().upper()
    if pending_answer not in {"A", "B", "C", "D"}:
        return False
    submitted_answer = str((parsed or {}).get("submitted_answer") or "").strip().upper()
    if submitted_answer and submitted_answer != pending_answer:
        return False
    pending_question = str(state.get("quiz_question") or "").strip()
    watcher_question = str((watcher or {}).get("question") or "").strip()
    if pending_question and watcher_question and _normalize_text(pending_question) != _normalize_text(watcher_question):
        return False
    return True


async def _confirm_quiz_answer_result(parsed, watcher):
    if (parsed or {}).get("status") not in {"correct", "wrong"}:
        return False
    identity_id = _get_quiz_result_identity_id(parsed, watcher)
    if identity_id is None or identity_id not in get_identity_ids():
        return False
    with use_identity(identity_id):
        if not _quiz_pending_matches_result(parsed, watcher):
            return False
        question = state.get("quiz_question") or "未记录题目"
        answer = str(state.get("quiz_answer") or "").strip().upper()
        result_label = "正确" if parsed.get("status") == "correct" else "错误"
        correct_answer = str(parsed.get("correct_answer") or "").strip().upper()
        correct_suffix = f"｜正确答案 {correct_answer}" if result_label == "错误" and correct_answer else ""
        await _finalize_quiz_success(
            f"🦴 作答发送成功：{result_label}｜{answer}{correct_suffix}｜{question}",
            identity_id=identity_id,
        )
    return True


async def _handle_quiz_pending_timeout(now):
    question = state.get("quiz_question") or "未记录题目"
    state["quiz_last_error"] = "题目已超时"
    await send_audit_log(
        f"⚠️ 玄骨考校题目已超时：{question}",
        scope="identity",
        send_as_id=get_current_identity_id(),
        limit=320,
    )
    clear_quiz_state(persist=True, keep_last_error=True)


async def _handle_quiz_answer_confirmation_timeout(now):
    question = state.get("quiz_question") or "未记录题目"
    answer = str(state.get("quiz_answer") or "").strip().upper()
    reply_to_msg_id = int(state.get("quiz_reply_to_msg_id", 0) or 0)
    retry_count = _get_quiz_retry_count()
    identity_id = get_current_identity_id()

    if retry_count >= QUIZ_ANSWER_MAX_RETRY_COUNT:
        state["quiz_last_error"] = f"作答发送失败：未收到正确/错误结果，已重试 {retry_count} 次"
        await send_audit_log(
            f"❌ 玄骨考校作答发送失败：未收到正确/错误结果，已重试 {retry_count} 次｜{answer}｜{question}",
            scope="identity",
            send_as_id=identity_id,
            limit=360,
        )
        clear_quiz_state(persist=True, keep_last_error=True)
        return

    retry_index = retry_count + 1
    reply_msg = await _send_quiz_answer(answer, reply_to_msg_id)
    state["quiz_retry_count"] = retry_index

    if reply_msg:
        state["next_quiz_time"] = float(now + QUIZ_ANSWER_CONFIRM_TIMEOUT_SEC)
        state["quiz_last_error"] = f"未收到正确/错误结果，已重试 {retry_index}/{QUIZ_ANSWER_MAX_RETRY_COUNT}"
        save_state()
        await send_audit_log(
            f"⚠️ 玄骨考校作答未收到结果，判定发送失败，已重试 {retry_index}/{QUIZ_ANSWER_MAX_RETRY_COUNT}：{answer}｜{question}",
            scope="identity",
            send_as_id=identity_id,
            limit=360,
        )
        return

    state["quiz_last_error"] = f"作答重试发送失败 {retry_index}/{QUIZ_ANSWER_MAX_RETRY_COUNT}"
    if retry_index >= QUIZ_ANSWER_MAX_RETRY_COUNT:
        await send_audit_log(
            f"❌ 玄骨考校作答重试发送失败，已达 {QUIZ_ANSWER_MAX_RETRY_COUNT} 次：{answer}｜{question}",
            scope="identity",
            send_as_id=identity_id,
            limit=360,
        )
        clear_quiz_state(persist=True, keep_last_error=True)
        return

    state["next_quiz_time"] = float(now + QUIZ_ANSWER_CONFIRM_TIMEOUT_SEC)
    save_state()
    await send_audit_log(
        f"⚠️ 玄骨考校作答重试发送失败 {retry_index}/{QUIZ_ANSWER_MAX_RETRY_COUNT}，1 分钟后继续重试：{answer}｜{question}",
        scope="identity",
        send_as_id=identity_id,
        limit=360,
    )


def get_quiz_status_text():
    reply_to_msg_id, deadline = _get_quiz_pending_state()
    lines = [
        "🦴 玄骨考校",
        f"- 当前题目：{state.get('quiz_question') or '暂无'}",
        f"- 匹配答案：{state.get('quiz_answer') or '未匹配'}",
        f"- 匹配方式：{state.get('quiz_match_mode') or '无'}",
        f"- 待回复消息ID：{reply_to_msg_id or '无'}",
        f"- 重试次数：{_get_quiz_retry_count()}/{QUIZ_ANSWER_MAX_RETRY_COUNT}",
        f"- 下次检查：{fmt_abs_ts(deadline)}（{fmt_remaining(deadline)}）",
        f"- 最近错误：{state.get('quiz_last_error') or '无'}",
    ]
    return "\n".join(lines)


def clear_quiz_state(*, persist=False, keep_last_error=False):
    state["next_quiz_time"] = 0
    state["quiz_reply_to_msg_id"] = 0
    state["quiz_question"] = ""
    state["quiz_options"] = {}
    state["quiz_answer"] = ""
    state["quiz_retry_count"] = 0
    state["quiz_match_mode"] = ""
    if not keep_last_error:
        state["quiz_last_error"] = ""
    state["quiz_last_matched_at"] = 0
    if persist:
        save_state()
    else:
        mark_dirty()


def _find_quiz_identity_id(text, *, enabled_only=True):
    compact_text = _normalize_text(text)
    if not compact_text:
        return None
    matched_ids = []
    for identity_id in get_identity_ids():
        if enabled_only and not get_identity_enabled(identity_id):
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


async def handle_quiz_learning_prompt(text, now, event=None):
    parsed = _parse_quiz_prompt(text)
    if not parsed:
        return False

    target_tag, target_key = _extract_quiz_target_tag(text)
    if not target_key:
        return False

    matched_answer, _ = _match_quiz_answer(parsed["question"], parsed["options"])
    watcher = {
        "target_tag": target_tag,
        "identity_id": _find_quiz_identity_id(text),
        "question": parsed["question"],
        "options": dict(parsed["options"]),
        "expire_at": float(now + float(parsed["timeout_sec"]) + QUIZ_RESULT_GRACE_SEC),
        "matched_answer": matched_answer,
    }
    _set_quiz_learning_watcher(target_key, watcher, persist=True)
    return True


async def handle_quiz_result_broadcast(text, now=None):
    # 统一解析三种结果类型
    parsed = _parse_quiz_result(text)
    result_type = None
    target_tag = ""
    target_key = ""
    correct_answer = ""
    submitted_answer = ""

    if parsed:
        result_type = parsed["status"]
        target_tag = parsed.get("target_tag", "")
        target_key = parsed.get("target_key", "")
        correct_answer = str(parsed.get("correct_answer") or "").strip().upper()
        submitted_answer = str(parsed.get("submitted_answer") or "").strip().upper()
    else:
        timeout_match = RE_QUIZ_RESULT_TIMEOUT.search(text or "")
        if timeout_match:
            result_type = "timeout"
            target_tag = f"@{str(timeout_match.group('tag') or '').strip()}"
            target_key = _normalize_quiz_target_key(target_tag)
        else:
            return False

    watcher = _get_quiz_learning_watcher(target_key) if target_key else None
    confirmed_answer = await _confirm_quiz_answer_result(parsed, watcher) if parsed else False
    if not watcher:
        return confirmed_answer

    question = str(watcher.get("question") or "").strip()
    options = dict(watcher.get("options") or {})
    target_tag = watcher.get("target_tag") or target_tag or "未知目标"
    identity_id = _normalize_quiz_identity_id(watcher.get("identity_id"))
    log_kwargs = _get_quiz_log_kwargs(identity_id, limit=520)

    # 查题库
    bank_answer, _ = _match_quiz_answer(question, options)
    in_bank = bool(bank_answer)

    _pop_quiz_learning_watcher(target_key, persist=True)

    if in_bank:
        # ---- 题目在题库内 ----
        if result_type == "correct":
            if bank_answer == correct_answer:
                await send_audit_log(
                    _format_quiz_brief_log("题库内答案正确 ✅", identity_id=identity_id, target_tag=target_tag),
                    **_get_quiz_log_kwargs(identity_id),
                )
            else:
                await send_audit_log(
                    "🦴 玄骨考校题库答案不一致，请人工处理\n"
                    f"- 目标: {mono(target_tag)}\n"
                    f"- 题目: {question}\n"
                    f"- 选项: {_format_quiz_options(options)}\n"
                    f"- 题库匹配: {bank_answer}\n"
                    f"- 提交答案: {submitted_answer}\n"
                    f"- 正确答案: {correct_answer}",
                    **log_kwargs,
                )
        elif result_type == "wrong":
            await send_audit_log(
                _format_quiz_brief_log("题库内作答错误", identity_id=identity_id, target_tag=target_tag),
                **_get_quiz_log_kwargs(identity_id),
            )
        elif result_type == "timeout":
            await send_audit_log(
                _format_quiz_brief_log("题库内超时未作答", identity_id=identity_id, target_tag=target_tag),
                **_get_quiz_log_kwargs(identity_id),
            )
    else:
        # ---- 题目不在题库 ----
        if result_type == "correct":
            status, payload = _save_quiz_bank_entry(question, options, correct_answer)
            if status == "added":
                await send_audit_log(
                    _format_quiz_brief_log(f"已记录新题 ✅ 答案：{correct_answer}", identity_id=identity_id, target_tag=target_tag),
                    **_get_quiz_log_kwargs(identity_id),
                )
            elif status == "exists":
                await send_audit_log(
                    _format_quiz_brief_log("题库内答案正确 ✅", identity_id=identity_id, target_tag=target_tag),
                    **_get_quiz_log_kwargs(identity_id),
                )
            elif status == "conflict":
                existing_answer = str((payload or {}).get("answer") or "").strip().upper()
                await send_audit_log(
                    "🦴 玄骨考校题库冲突，请人工处理\n"
                    f"- 目标: {mono(target_tag)}\n"
                    f"- 题目: {question}\n"
                    f"- 选项: {_format_quiz_options(options)}\n"
                    f"- 题库答案: {existing_answer}\n"
                    f"- 正确答案: {correct_answer}",
                    **log_kwargs,
                )
            else:
                await send_audit_log(
                    f"🦴 玄骨考校题库记录失败({status})\n"
                    f"- 目标: {mono(target_tag)}\n"
                    f"- 题目: {question}\n"
                    f"- 选项: {_format_quiz_options(options)}\n"
                    f"- 正确答案: {correct_answer}",
                    **log_kwargs,
                )
        elif result_type == "wrong":
            submitted_text = str(options.get(submitted_answer) or "").strip()
            correct_text = str(options.get(correct_answer) or "").strip()
            status, payload = _save_quiz_bank_entry(question, options, correct_answer)
            if status == "added":
                await send_audit_log(
                    f"🦴 已记录新题 ✅ 答案：{correct_answer}\n"
                    f"- 来源: 群内作答错误\n"
                    f"- 目标: {mono(target_tag)}\n"
                    f"- 题目: {question}\n"
                    f"- 选项: {_format_quiz_options(options)}\n"
                    f"- 提交: {submitted_answer}.{submitted_text}\n"
                    f"- 正确: {correct_answer}.{correct_text}",
                    **log_kwargs,
                )
            elif status == "exists":
                await send_audit_log(
                    _format_quiz_brief_log("题库已收录错误结果中的正确答案", identity_id=identity_id, target_tag=target_tag),
                    **_get_quiz_log_kwargs(identity_id),
                )
            elif status == "conflict":
                existing_answer = str((payload or {}).get("answer") or "").strip().upper()
                await send_audit_log(
                    "🦴 玄骨考校题库冲突，请人工处理\n"
                    f"- 目标: {mono(target_tag)}\n"
                    f"- 题目: {question}\n"
                    f"- 选项: {_format_quiz_options(options)}\n"
                    f"- 题库答案: {existing_answer}\n"
                    f"- 正确答案: {correct_answer}\n"
                    f"- 提交: {submitted_answer}.{submitted_text}",
                    **log_kwargs,
                )
            else:
                await send_audit_log(
                    f"🦴 玄骨考校题库记录失败({status})\n"
                    f"- 目标: {mono(target_tag)}\n"
                    f"- 题目: {question}\n"
                    f"- 选项: {_format_quiz_options(options)}\n"
                    f"- 正确答案: {correct_answer}\n"
                    f"- 提交: {submitted_answer}.{submitted_text}",
                    **log_kwargs,
                )
        elif result_type == "timeout":
            await send_audit_log(
                "🦴 玄骨考校题库未收录，超时未作答\n"
                f"- 目标: {mono(target_tag)}\n"
                f"- 题目: {question}\n"
                f"- 选项: {_format_quiz_options(options)}",
                **log_kwargs,
            )

    return True


async def handle_quiz_prompt(text, now, event):
    if not state.get("quiz_enabled"):
        return False
    if _has_active_quiz_pending(now):
        return False

    parsed, identity_id = _match_quiz_prompt_for_current_identity(text)
    if not parsed:
        return False

    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    answer, match_mode = _match_quiz_answer(parsed["question"], parsed["options"])
    if not answer:
        _set_quiz_pending(
            parsed["question"],
            parsed["options"],
            "",
            reply_to_msg_id,
            now + float(parsed["timeout_sec"]),
            last_error="题库未命中",
            last_matched_at=0,
        )
        save_state()
        await send_audit_log(
            f"🦴 题库未命中：{parsed['question']}｜{_format_quiz_options(parsed['options'])}",
            scope="identity",
            send_as_id=identity_id,
            limit=360,
        )
        return True

    _set_quiz_pending(
        parsed["question"],
        parsed["options"],
        answer,
        reply_to_msg_id,
        now + QUIZ_ANSWER_CONFIRM_TIMEOUT_SEC,
        last_error="",
        last_matched_at=now,
        match_mode=match_mode,
    )
    save_state()

    reply_msg = await _send_quiz_answer(answer, reply_to_msg_id)
    if not reply_msg:
        _set_quiz_error_and_save("作答发送失败")
        await send_audit_log(
            f"❌ 玄骨考校作答发送失败：{parsed['question']}",
            scope="identity",
            send_as_id=identity_id,
            limit=320,
        )
        return True

    await send_audit_log(
        f"🦴 已发送作答，等待结果确认：{answer}｜{match_mode}｜{parsed['question']}",
        scope="identity",
        send_as_id=identity_id,
        limit=360,
    )
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
    reply_to_msg_id, next_quiz_time = _get_quiz_pending_state()
    if reply_to_msg_id <= 0 or next_quiz_time <= 0 or now < next_quiz_time:
        return
    answer = str(state.get("quiz_answer") or "").strip().upper()
    if answer in {"A", "B", "C", "D"}:
        await _handle_quiz_answer_confirmation_timeout(now)
    else:
        await _handle_quiz_pending_timeout(now)


__all__ = [
    "clear_quiz_state",
    "get_quiz_status_text",
    "handle_quiz_learning_prompt",
    "handle_quiz_prompt",
    "handle_quiz_result_broadcast",
    "run_quiz_learning_scheduler",
    "run_quiz_scheduler",
]
