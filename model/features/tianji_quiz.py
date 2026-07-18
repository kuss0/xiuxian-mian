import asyncio
import json
import os
import random
import re
import time
from types import SimpleNamespace

from ..config import TIANJI_QUIZ_BANK_FILE
from ..message_log_recovery import iter_message_log_entries_between
from ..persistence import save_state
from ..runtime import mono, send_audit_log, send_game_command
from ..state import get_identity_ids, get_send_as_tags, state


TIANJI_QUIZ_PROMPT_KEYWORDS = ("【天机考验】", "直接回复本消息", "回答错误或超时")
TIANJI_QUIZ_OPTIONS = ("A", "B", "C", "D")
TIANJI_QUIZ_DELAY_MIN_SEC = 10
TIANJI_QUIZ_DELAY_MAX_SEC = 40
TIANJI_QUIZ_DEFAULT_TIMEOUT_SEC = 2 * 60
TIANJI_QUIZ_DEADLINE_BUFFER_SEC = 5
TIANJI_QUIZ_RESULT_TIMEOUT_SEC = 20
TIANJI_QUIZ_RETRY_DELAY_MIN_SEC = 5
TIANJI_QUIZ_RETRY_DELAY_MAX_SEC = 15
TIANJI_QUIZ_MAX_RETRY_COUNT = 1
TIANJI_QUIZ_RESULT_LOG_LOOKBACK_SEC = 10 * 60
RE_TIANJI_TARGET = re.compile(r"@([^\s，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+)")
RE_TIANJI_OPTION = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$", re.M)
RE_TIANJI_TIMEOUT_MIN = re.compile(r"请在\s*(\d+)\s*分钟")
RE_TIANJI_TIMEOUT_SEC = re.compile(r"请在\s*(\d+)\s*秒")
RE_TIANJI_WHITESPACE = re.compile(r"\s+")
RE_TIANJI_IDENTITY_SEPARATORS = re.compile(r"[\s@，。！？、；：:,.!?\[\]【】()（）<>《》“”\"'`]+")
_TIANJI_QUIZ_SCHEDULER_LOCK = asyncio.Lock()


def _is_tianji_quiz_prompt(text):
    raw_text = str(text or "")
    return all(keyword in raw_text for keyword in TIANJI_QUIZ_PROMPT_KEYWORDS)


def _normalize_text(text):
    return RE_TIANJI_WHITESPACE.sub("", str(text or "")).strip().lower()


def _normalize_identity_text(text):
    return RE_TIANJI_IDENTITY_SEPARATORS.sub("", str(text or "").strip().lstrip("@")).lower()


def _extract_tianji_target(text):
    matched = RE_TIANJI_TARGET.search(str(text or ""))
    return f"@{matched.group(1).strip()}" if matched else "未知目标"


def _extract_tianji_question_text(text):
    raw_text = str(text or "")
    before_options = RE_TIANJI_OPTION.split(raw_text, maxsplit=1)[0]
    lines = [line.strip() for line in before_options.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("【") or line.startswith("请在") or "道友" in line:
            continue
        return line
    return ""


def _parse_tianji_timeout_sec(text):
    raw_text = str(text or "")
    minute_match = RE_TIANJI_TIMEOUT_MIN.search(raw_text)
    if minute_match:
        return max(1, int(minute_match.group(1)) * 60)
    second_match = RE_TIANJI_TIMEOUT_SEC.search(raw_text)
    if second_match:
        return max(1, int(second_match.group(1)))
    return TIANJI_QUIZ_DEFAULT_TIMEOUT_SEC


def parse_tianji_quiz_prompt(text):
    if not _is_tianji_quiz_prompt(text):
        return None

    raw_text = str(text or "")
    options = {key: value.strip() for key, value in RE_TIANJI_OPTION.findall(raw_text)}
    question = _extract_tianji_question_text(raw_text)
    parsed = {
        "target": _extract_tianji_target(raw_text),
        "question": question,
        "options": options,
        "timeout_sec": _parse_tianji_timeout_sec(raw_text),
        "is_choice": bool(question and all(options.get(key) for key in TIANJI_QUIZ_OPTIONS)),
        "raw_text": raw_text.strip(),
    }
    return parsed


def _format_tianji_options(options):
    return " | ".join(
        f"{key}.{str((options or {}).get(key) or '').strip()}"
        for key in TIANJI_QUIZ_OPTIONS
        if str((options or {}).get(key) or "").strip()
    )


def _format_tianji_answer_detail(answer, options):
    normalized_answer = str(answer or "").strip().upper()
    if normalized_answer not in TIANJI_QUIZ_OPTIONS:
        return normalized_answer or "未匹配"
    option_text = str((options or {}).get(normalized_answer) or "").strip()
    return f"{normalized_answer}.{option_text}" if option_text else normalized_answer


def _get_tianji_item_options(item):
    options = (item or {}).get("options", {})
    return dict(options) if isinstance(options, dict) else {}


def _load_tianji_quiz_bank_items():
    try:
        with open(TIANJI_QUIZ_BANK_FILE, "r", encoding="utf-8") as f:
            raw_items = json.load(f)
    except FileNotFoundError:
        return []
    except Exception:
        return None
    return raw_items if isinstance(raw_items, list) else []


def _write_tianji_quiz_bank_items(items):
    os.makedirs(os.path.dirname(TIANJI_QUIZ_BANK_FILE), exist_ok=True)
    with open(TIANJI_QUIZ_BANK_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _build_tianji_quiz_bank_record(question, options, answer=""):
    normalized_question = str(question or "").strip()
    normalized_options = {
        key: str((options or {}).get(key) or "").strip()
        for key in TIANJI_QUIZ_OPTIONS
    }
    normalized_answer = str(answer or "").strip().upper()
    if normalized_answer and normalized_answer not in TIANJI_QUIZ_OPTIONS:
        return None
    if not normalized_question or any(not normalized_options.get(key) for key in TIANJI_QUIZ_OPTIONS):
        return None
    return {
        "question": normalized_question,
        "A": normalized_options["A"],
        "B": normalized_options["B"],
        "C": normalized_options["C"],
        "D": normalized_options["D"],
        "answer": normalized_answer,
    }


def _get_tianji_quiz_bank_record_keys(record):
    return (
        _normalize_text((record or {}).get("question")),
        {
            key: _normalize_text((record or {}).get(key))
            for key in TIANJI_QUIZ_OPTIONS
        },
    )


def _classify_tianji_quiz_bank_record(existing_items, new_record):
    question_key, option_keys = _get_tianji_quiz_bank_record_keys(new_record)
    if not question_key or any(not value for value in option_keys.values()):
        return "invalid", None

    for item in existing_items or []:
        if not isinstance(item, dict):
            continue
        existing_question_key, existing_option_keys = _get_tianji_quiz_bank_record_keys(item)
        if existing_question_key != question_key:
            continue
        if existing_option_keys == option_keys:
            return "exists", item
        return "conflict", item
    return "new", None


def save_tianji_quiz_bank_entry(question, options, answer=""):
    record = _build_tianji_quiz_bank_record(question, options, answer)
    if not record:
        return "invalid", None
    items = _load_tianji_quiz_bank_items()
    if items is None:
        return "io_error", None
    status, existing = _classify_tianji_quiz_bank_record(items, record)
    if status == "exists":
        normalized_answer = str(answer or "").strip().upper()
        if not normalized_answer:
            return "exists", existing
        existing_answer = str((existing or {}).get("answer") or "").strip().upper()
        if existing_answer == normalized_answer:
            return "exists", existing
        if not existing_answer:
            try:
                existing["answer"] = normalized_answer
                _write_tianji_quiz_bank_items(items)
            except Exception:
                return "io_error", None
            return "updated", existing
        return "conflict", existing
    if status != "new":
        return status, existing
    try:
        items.append(record)
        _write_tianji_quiz_bank_items(items)
    except Exception:
        return "io_error", None
    return "added", record


def _match_tianji_quiz_answer(question, options):
    record = _build_tianji_quiz_bank_record(question, options)
    if not record:
        return "", "invalid"
    items = _load_tianji_quiz_bank_items()
    if items is None:
        return "", "io_error"
    status, existing = _classify_tianji_quiz_bank_record(items, record)
    if status != "exists":
        return "", status
    answer = str((existing or {}).get("answer") or "").strip().upper()
    if answer in TIANJI_QUIZ_OPTIONS:
        return answer, "bank"
    return "", "answer_missing"


def _get_tianji_quiz_pending_map():
    pending = state.get("tianji_quiz_pending", {})
    return dict(pending) if isinstance(pending, dict) else {}


def _set_tianji_quiz_pending_map(pending):
    state["tianji_quiz_pending"] = dict(pending or {})
    save_state()


def _get_event_pending_key(event, parsed):
    msg_id = int(getattr(event, "id", 0) or 0)
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    if msg_id > 0 and chat_id:
        return f"{chat_id}:{msg_id}"
    if msg_id > 0:
        return f"msg:{msg_id}"
    return f"{parsed.get('target') or 'unknown'}:{parsed.get('question') or ''}"


def _get_identity_tag_keys(identity_id):
    return {
        _normalize_identity_text(tag)
        for tag in get_send_as_tags(identity_id)
        if _normalize_identity_text(tag)
    }


def _find_target_identity_id(target, text=""):
    target_key = _normalize_identity_text(target)
    compact_text = _normalize_identity_text(text)
    matched_ids = []
    for identity_id in get_identity_ids():
        tag_keys = _get_identity_tag_keys(identity_id)
        if target_key and target_key in tag_keys:
            matched_ids.append(int(identity_id))
        elif not target_key and any(tag_key and tag_key in compact_text for tag_key in tag_keys):
            matched_ids.append(int(identity_id))
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _build_tianji_quiz_pending_item(parsed, identity_id, answer, event, now):
    delay_sec = random.uniform(TIANJI_QUIZ_DELAY_MIN_SEC, TIANJI_QUIZ_DELAY_MAX_SEC)
    timeout_sec = float(parsed.get("timeout_sec") or TIANJI_QUIZ_DEFAULT_TIMEOUT_SEC)
    deadline_at = float(now) + timeout_sec
    due_at = float(now) + delay_sec
    if due_at >= deadline_at:
        due_at = max(float(now) + 1, deadline_at - TIANJI_QUIZ_DEADLINE_BUFFER_SEC)
    return {
        "target": parsed.get("target") or "未知目标",
        "identity_id": int(identity_id),
        "question": parsed.get("question") or "",
        "options": dict(parsed.get("options") or {}),
        "answer": answer,
        "due_at": float(due_at),
        "deadline_at": float(deadline_at),
        "created_at": float(now),
        "msg_id": int(getattr(event, "id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "retry_count": 0,
        "phase": "queued",
        "sent_msg_id": 0,
        "result_due_at": 0,
    }


def _schedule_tianji_quiz_due_task(due_at):
    # Global scheduler polling owns due processing; do not create per-item sleep tasks.
    _ = due_at


async def _queue_tianji_quiz_answer(parsed, now, event):
    answer, _ = _match_tianji_quiz_answer(parsed.get("question"), parsed.get("options"))
    if not answer:
        return
    identity_id = _find_target_identity_id(parsed.get("target"), parsed.get("raw_text"))
    if identity_id is None:
        return
    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    answer_detail = _format_tianji_answer_detail(answer, parsed.get("options"))
    if reply_to_msg_id <= 0:
        await send_audit_log(
            "🧭 天机考验无法自动作答：缺少题目消息ID\n"
            f"- 目标: {mono(parsed.get('target') or '未知目标')}\n"
            f"- 答案: {answer_detail}\n"
            f"- 题目: {parsed.get('question') or '未知题目'}",
            scope="global",
            limit=520,
        )
        return

    pending_key = _get_event_pending_key(event, parsed)
    pending = _get_tianji_quiz_pending_map()
    if pending_key in pending:
        return

    item = _build_tianji_quiz_pending_item(parsed, identity_id, answer, event, now)
    pending[pending_key] = item
    _set_tianji_quiz_pending_map(pending)
    _schedule_tianji_quiz_due_task(item["due_at"])
    await send_audit_log(
        "🧭 天机考验已排队作答\n"
        f"- 目标: {mono(item.get('target') or '未知目标')}\n"
        f"- 答案: {answer_detail}\n"
        f"- 题目: {item.get('question') or '未知题目'}\n"
        f"- 延迟: {int(max(0, item['due_at'] - float(now)))}秒",
        scope="global",
        limit=520,
    )


def _get_event_reply_to_msg_id(event):
    reply_header = getattr(event, "reply_to", None)
    return int(getattr(reply_header, "reply_to_msg_id", 0) or 0)


def _parse_tianji_quiz_result(text):
    raw_text = str(text or "").strip()
    if not raw_text or _is_tianji_quiz_prompt(raw_text):
        return None
    target = _extract_tianji_target(raw_text)
    if "考验通过" in raw_text and "气息已恢复正常" in raw_text:
        return {"status": "passed", "label": "考验通过", "target": target, "raw_text": raw_text}
    if "考验超时" in raw_text and "嫌疑更重" in raw_text:
        return {"status": "timeout", "label": "考验超时", "target": target, "raw_text": raw_text}
    if "回答错误" in raw_text and "嫌疑更重" in raw_text:
        return {"status": "wrong", "label": "回答错误", "target": target, "raw_text": raw_text}
    if "【天道示警】" in raw_text and "未能通过天机考验" in raw_text:
        return {"status": "warning", "label": "天道示警", "target": target, "raw_text": raw_text}
    if "【打入天牢】" in raw_text:
        return {"status": "jail", "label": "打入天牢", "target": target, "raw_text": raw_text}
    return None


def _format_tianji_result_excerpt(text):
    first_line = str(text or "").strip().splitlines()[0] if str(text or "").strip() else "未知结果"
    return first_line[:80].rstrip()


def _get_message_text(message):
    return str(getattr(message, "raw_text", None) or getattr(message, "message", None) or "")


def _parse_tianji_submitted_answer(text):
    answer = str(text or "").strip().upper()
    return answer if answer in TIANJI_QUIZ_OPTIONS else ""


async def _get_tianji_answer_prompt_message(answer_message):
    if not answer_message:
        return None
    if _is_tianji_quiz_prompt(_get_message_text(answer_message)):
        return answer_message
    try:
        return await answer_message.get_reply_message()
    except Exception:
        return None


async def _learn_tianji_quiz_answer_from_result(parsed_result, reply_to=None):
    if (parsed_result or {}).get("status") != "passed" or reply_to is None:
        return False

    answer_text = _get_message_text(reply_to)
    prompt_message = await _get_tianji_answer_prompt_message(reply_to)
    parsed_prompt = parse_tianji_quiz_prompt(_get_message_text(prompt_message))
    if not parsed_prompt or not parsed_prompt.get("is_choice"):
        return False

    answer = _parse_tianji_submitted_answer(answer_text)
    if answer not in TIANJI_QUIZ_OPTIONS:
        return False

    question = parsed_prompt.get("question") or ""
    options = parsed_prompt.get("options") or {}
    status, payload = save_tianji_quiz_bank_entry(question, options, answer)
    target = parsed_prompt.get("target") or (parsed_result or {}).get("target") or "未知目标"
    if status == "added":
        await send_audit_log(
            "🧭 天机考验已记录新题 ✅\n"
            f"- 目标: {mono(target)}\n"
            f"- 答案: {_format_tianji_answer_detail(answer, options)}\n"
            f"- 题目: {question}\n"
            f"- 选项: {_format_tianji_options(options)}",
            scope="global",
            limit=620,
        )
        return True
    if status == "updated":
        await send_audit_log(
            f"🧭 天机考验已补全题库答案 ✅：{mono(target)}｜{_format_tianji_answer_detail(answer, options)}｜题目：{question}",
            scope="global",
            limit=520,
        )
        return True
    if status == "conflict":
        existing_answer = str((payload or {}).get("answer") or "").strip().upper()
        await send_audit_log(
            "🧭 天机考验题库答案冲突，请手动处理\n"
            f"- 目标: {mono(target)}\n"
            f"- 题目: {question}\n"
            f"- 选项: {_format_tianji_options(options)}\n"
            f"- 题库答案: {_format_tianji_answer_detail(existing_answer, payload or options)}\n"
            f"- 通过答案: {_format_tianji_answer_detail(answer, options)}",
            scope="global",
            limit=700,
        )
        return True
    if status in {"invalid", "io_error"}:
        await send_audit_log(
            f"🧭 天机考验题库答案记录失败({status})：{mono(target)}｜{_format_tianji_answer_detail(answer, options)}｜题目：{question}",
            scope="global",
            limit=520,
        )
        return True
    return False


def _has_tianji_answer_sent(item):
    return int((item or {}).get("sent_msg_id", 0) or 0) > 0 or float((item or {}).get("sent_at", 0) or 0) > 0


def _find_tianji_result_pending(parsed_result, event):
    pending = _get_tianji_quiz_pending_map()
    if not pending:
        return "", None, pending

    reply_to_msg_id = _get_event_reply_to_msg_id(event)
    if reply_to_msg_id > 0:
        for pending_key, item in pending.items():
            if not _has_tianji_answer_sent(item):
                continue
            if int((item or {}).get("sent_msg_id", 0) or 0) == reply_to_msg_id:
                return pending_key, item, pending

    target_key = _normalize_identity_text((parsed_result or {}).get("target"))
    if target_key:
        matched = []
        for pending_key, item in pending.items():
            if not _has_tianji_answer_sent(item):
                continue
            if _normalize_identity_text((item or {}).get("target")) == target_key:
                matched.append((pending_key, item))
        if len(matched) == 1:
            pending_key, item = matched[0]
            return pending_key, item, pending

    return "", None, pending


async def handle_tianji_quiz_result_broadcast(text, now=None, event=None, reply_to=None):
    parsed_result = _parse_tianji_quiz_result(text)
    reply_to_msg_id = _get_event_reply_to_msg_id(event)
    if parsed_result is None and reply_to_msg_id <= 0:
        return False

    learned = await _learn_tianji_quiz_answer_from_result(parsed_result, reply_to=reply_to)
    pending_key, item, pending = _find_tianji_result_pending(parsed_result, event)
    if not item:
        return learned

    raw_text = str(text or "").strip()
    label = (parsed_result or {}).get("label") or "收到回复"
    target = str((item or {}).get("target") or (parsed_result or {}).get("target") or "未知目标")
    question = str((item or {}).get("question") or "未知题目")
    answer_detail = _format_tianji_answer_detail((item or {}).get("answer"), _get_tianji_item_options(item))
    pending.pop(pending_key, None)
    _set_tianji_quiz_pending_map(pending)
    await send_audit_log(
        "🧭 天机考验收到结果，停止重试\n"
        f"- 目标: {mono(target)}\n"
        f"- 作答: {answer_detail}\n"
        f"- 题目: {question}\n"
        f"- 结果: {label}\n"
        f"- 内容: {_format_tianji_result_excerpt(raw_text)}",
        scope="global",
        limit=620,
    )
    return True


async def handle_tianji_quiz_prompt(text, now=None, event=None):
    parsed = parse_tianji_quiz_prompt(text)
    if not parsed:
        return False
    if now is None:
        now = time.time()

    target = parsed.get("target") or "未知目标"
    question = parsed.get("question") or ""
    if not parsed.get("is_choice"):
        await send_audit_log(
            "🧭 天机考验非选择题，请手动处理\n"
            f"- 目标: {mono(target)}\n"
            f"- 内容: {parsed.get('raw_text') or ''}",
            scope="global",
            limit=700,
        )
        return True

    status, payload = save_tianji_quiz_bank_entry(question, parsed.get("options"))
    if status == "added":
        await send_audit_log(
            "🧭 天机考验新增题目\n"
            f"- 目标: {mono(target)}\n"
            f"- 题目: {question}\n"
            f"- 选项: {_format_tianji_options(parsed.get('options'))}",
            scope="global",
            limit=520,
        )
    elif status == "conflict":
        await send_audit_log(
            "🧭 天机考验题库冲突，请手动处理\n"
            f"- 目标: {mono(target)}\n"
            f"- 题目: {question}\n"
            f"- 新选项: {_format_tianji_options(parsed.get('options'))}\n"
            f"- 已有选项: {_format_tianji_options(payload or {})}",
            scope="global",
            limit=700,
        )
    elif status in {"invalid", "io_error"}:
        await send_audit_log(
            f"🧭 天机考验题库记录失败({status})\n"
            f"- 目标: {mono(target)}\n"
            f"- 题目: {question}\n"
            f"- 选项: {_format_tianji_options(parsed.get('options'))}",
            scope="global",
            limit=520,
        )

    await _queue_tianji_quiz_answer(parsed, now, event)
    return True


async def _recover_tianji_quiz_result_from_message_log(item, now):
    item = item if isinstance(item, dict) else {}
    chat_id = int(item.get("chat_id", 0) or 0)
    sent_msg_id = int(item.get("sent_msg_id", 0) or 0)
    target_key = _normalize_identity_text(item.get("target"))
    start_ts = max(0.0, float(now or 0) - TIANJI_QUIZ_RESULT_LOG_LOOKBACK_SEC)
    start_ts = max(start_ts, float(item.get("sent_at", 0) or 0) - 5)
    for entry, entry_ts in iter_message_log_entries_between(start_ts, float(now or 0) + 5):
        if chat_id and int((entry or {}).get("chat_id") or 0) != chat_id:
            continue
        if str((entry or {}).get("event_type") or "") not in {"message", "edit"}:
            continue
        text = str((entry or {}).get("text") or "")
        parsed_result = _parse_tianji_quiz_result(text)
        reply_to_msg_id = int((entry or {}).get("reply_to_msg_id", 0) or 0)
        direct_reply = sent_msg_id > 0 and reply_to_msg_id == sent_msg_id
        target_match = bool(
            parsed_result
            and target_key
            and _normalize_identity_text(parsed_result.get("target")) == target_key
        )
        if not direct_reply and not target_match:
            continue
        event = SimpleNamespace(reply_to=SimpleNamespace(reply_to_msg_id=reply_to_msg_id))
        if await handle_tianji_quiz_result_broadcast(
            text,
            now=entry_ts or now,
            event=event,
        ):
            return True
    return False


def _schedule_tianji_quiz_retry(item, now):
    retry_count = int((item or {}).get("retry_count", 0) or 0) + 1
    delay_sec = random.uniform(TIANJI_QUIZ_RETRY_DELAY_MIN_SEC, TIANJI_QUIZ_RETRY_DELAY_MAX_SEC)
    item["retry_count"] = retry_count
    item["phase"] = "queued"
    item["due_at"] = float(now + delay_sec)
    item["result_due_at"] = 0
    return retry_count, delay_sec


async def run_tianji_quiz_scheduler(now):
    async with _TIANJI_QUIZ_SCHEDULER_LOCK:
        await _run_tianji_quiz_scheduler_locked(now)


async def _run_tianji_quiz_scheduler_locked(now):
    pending = _get_tianji_quiz_pending_map()
    if not pending:
        return

    changed = False
    for pending_key, item in list(pending.items()):
        identity_id = int((item or {}).get("identity_id", 0) or 0)
        target = str((item or {}).get("target") or "未知目标")
        answer = str((item or {}).get("answer") or "").strip().upper()
        options = _get_tianji_item_options(item)
        answer_detail = _format_tianji_answer_detail(answer, options)
        question = str((item or {}).get("question") or "未知题目")
        msg_id = int((item or {}).get("msg_id", 0) or 0)
        due_at = float((item or {}).get("due_at", 0) or 0)
        deadline_at = float((item or {}).get("deadline_at", 0) or 0)
        phase = str((item or {}).get("phase") or "queued")
        retry_count = int((item or {}).get("retry_count", 0) or 0)

        if not answer or msg_id <= 0 or (deadline_at > 0 and now >= deadline_at):
            pending.pop(pending_key, None)
            changed = True
            if answer and msg_id > 0:
                await send_audit_log(
                    f"🧭 天机考验作答已超时：{mono(target)}｜{answer_detail}｜题目：{question}",
                    scope="global",
                    limit=520,
                )
            continue
        if due_at <= 0 or now < due_at:
            continue
        if identity_id <= 0 or identity_id not in get_identity_ids():
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(
                f"🧭 天机考验未发送：{mono(target)} 身份不存在｜{answer_detail}｜题目：{question}",
                scope="global",
                limit=520,
            )
            continue

        if phase == "waiting_result":
            if await _recover_tianji_quiz_result_from_message_log(item, now):
                pending.pop(pending_key, None)
                changed = True
                continue
            if retry_count >= TIANJI_QUIZ_MAX_RETRY_COUNT:
                pending.pop(pending_key, None)
                changed = True
                await send_audit_log(
                    f"🧭 天机考验 20 秒未收到结果，已重试 {retry_count} 次，停止重试：{mono(target)}｜{answer_detail}｜题目：{question}",
                    scope="global",
                    limit=520,
                )
                continue
            retry_count, delay_sec = _schedule_tianji_quiz_retry(item, now)
            pending[pending_key] = item
            _schedule_tianji_quiz_due_task(item["due_at"])
            changed = True
            await send_audit_log(
                f"🧭 天机考验 20 秒未收到结果，{int(delay_sec)} 秒后重试 {retry_count}/{TIANJI_QUIZ_MAX_RETRY_COUNT}：{mono(target)}｜{answer_detail}｜题目：{question}",
                scope="global",
                limit=520,
            )
            continue

        chain_id = f"tianji_quiz:{pending_key}"
        msg = await send_game_command(
            answer,
            track=False,
            reply_to=msg_id,
            send_as_id=identity_id,
            priority="p0",
            source_module="天机考验",
            op_id=f"{chain_id}:answer",
            chain_id=chain_id,
        )
        if msg:
            sent_at = float(getattr(msg, "sent_at", 0) or time.time())
            item["phase"] = "waiting_result"
            item["sent_msg_id"] = int(getattr(msg, "id", 0) or 0)
            item["sent_at"] = sent_at
            item["result_due_at"] = float(sent_at + TIANJI_QUIZ_RESULT_TIMEOUT_SEC)
            item["due_at"] = item["result_due_at"]
            pending[pending_key] = item
            _schedule_tianji_quiz_due_task(item["due_at"])
            changed = True
            await send_audit_log(
                f"🧭 天机考验已作答，等待结果：{mono(target)}｜{answer_detail}｜题目：{question}",
                scope="global",
                limit=520,
            )
            continue

        if retry_count >= TIANJI_QUIZ_MAX_RETRY_COUNT:
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(
                f"🧭 天机考验作答发送失败，已重试 {retry_count} 次：{mono(target)}｜{answer_detail}｜题目：{question}",
                scope="global",
                limit=520,
            )
            continue

        retry_count, delay_sec = _schedule_tianji_quiz_retry(item, time.time())
        pending[pending_key] = item
        _schedule_tianji_quiz_due_task(item["due_at"])
        changed = True
        await send_audit_log(
            f"🧭 天机考验作答发送失败，{int(delay_sec)} 秒后重试 {retry_count}/{TIANJI_QUIZ_MAX_RETRY_COUNT}：{mono(target)}｜{answer_detail}｜题目：{question}",
            scope="global",
            limit=520,
        )

    if changed:
        _set_tianji_quiz_pending_map(pending)


__all__ = [
    "handle_tianji_quiz_prompt",
    "handle_tianji_quiz_result_broadcast",
    "parse_tianji_quiz_prompt",
    "run_tianji_quiz_scheduler",
    "save_tianji_quiz_bank_entry",
]
