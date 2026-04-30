import json
import os
import random
import re
import time

from ..config import TIANJI_QUIZ_BANK_FILE
from ..persistence import save_state
from ..runtime import mono, send_audit_log, send_game_command
from ..state import get_identity_ids, get_send_as_tags, state


TIANJI_QUIZ_PROMPT_KEYWORDS = ("【天机考验】", "直接回复本消息", "回答错误或超时")
TIANJI_QUIZ_OPTIONS = ("A", "B", "C", "D")
TIANJI_QUIZ_DELAY_MIN_SEC = 10
TIANJI_QUIZ_DELAY_MAX_SEC = 40
TIANJI_QUIZ_DEFAULT_TIMEOUT_SEC = 2 * 60
TIANJI_QUIZ_DEADLINE_BUFFER_SEC = 5
TIANJI_QUIZ_RETRY_DELAY_SEC = 10
RE_TIANJI_TARGET = re.compile(r"@([^\s，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+)")
RE_TIANJI_OPTION = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$", re.M)
RE_TIANJI_TIMEOUT_MIN = re.compile(r"请在\s*(\d+)\s*分钟")
RE_TIANJI_TIMEOUT_SEC = re.compile(r"请在\s*(\d+)\s*秒")
RE_TIANJI_WHITESPACE = re.compile(r"\s+")
RE_TIANJI_IDENTITY_SEPARATORS = re.compile(r"[\s@，。！？、；：:,.!?\[\]【】()（）<>《》“”\"'`]+")


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


def _build_tianji_quiz_bank_record(question, options):
    normalized_question = str(question or "").strip()
    normalized_options = {
        key: str((options or {}).get(key) or "").strip()
        for key in TIANJI_QUIZ_OPTIONS
    }
    if not normalized_question or any(not normalized_options.get(key) for key in TIANJI_QUIZ_OPTIONS):
        return None
    return {
        "question": normalized_question,
        "A": normalized_options["A"],
        "B": normalized_options["B"],
        "C": normalized_options["C"],
        "D": normalized_options["D"],
        "answer": "",
    }


def _classify_tianji_quiz_bank_record(existing_items, new_record):
    question_key = _normalize_text(new_record.get("question"))
    option_keys = {
        key: _normalize_text(new_record.get(key))
        for key in TIANJI_QUIZ_OPTIONS
    }
    if not question_key or any(not value for value in option_keys.values()):
        return "invalid", None

    for item in existing_items or []:
        if not isinstance(item, dict):
            continue
        if _normalize_text(item.get("question")) != question_key:
            continue
        existing_option_keys = {
            key: _normalize_text(item.get(key))
            for key in TIANJI_QUIZ_OPTIONS
        }
        if existing_option_keys == option_keys:
            return "exists", item
        return "conflict", item
    return "new", None


def save_tianji_quiz_bank_entry(question, options):
    record = _build_tianji_quiz_bank_record(question, options)
    if not record:
        return "invalid", None
    items = _load_tianji_quiz_bank_items()
    if items is None:
        return "io_error", None
    status, existing = _classify_tianji_quiz_bank_record(items, record)
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
        "answer": answer,
        "due_at": float(due_at),
        "deadline_at": float(deadline_at),
        "created_at": float(now),
        "msg_id": int(getattr(event, "id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "retry_count": 0,
    }


async def _queue_tianji_quiz_answer(parsed, now, event):
    answer, _ = _match_tianji_quiz_answer(parsed.get("question"), parsed.get("options"))
    if not answer:
        return
    identity_id = _find_target_identity_id(parsed.get("target"), parsed.get("raw_text"))
    if identity_id is None:
        return
    reply_to_msg_id = int(getattr(event, "id", 0) or 0)
    if reply_to_msg_id <= 0:
        await send_audit_log(
            "🧭 天机考验无法自动作答：缺少题目消息ID\n"
            f"- 目标: {mono(parsed.get('target') or '未知目标')}\n"
            f"- 答案: {answer}",
            scope="global",
            limit=360,
        )
        return

    pending_key = _get_event_pending_key(event, parsed)
    pending = _get_tianji_quiz_pending_map()
    if pending_key in pending:
        return

    item = _build_tianji_quiz_pending_item(parsed, identity_id, answer, event, now)
    pending[pending_key] = item
    _set_tianji_quiz_pending_map(pending)
    await send_audit_log(
        "🧭 天机考验已排队作答\n"
        f"- 目标: {mono(item.get('target') or '未知目标')}\n"
        f"- 答案: {answer}\n"
        f"- 延迟: {int(max(0, item['due_at'] - float(now)))}秒",
        scope="global",
        limit=360,
    )


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


async def run_tianji_quiz_scheduler(now):
    pending = _get_tianji_quiz_pending_map()
    if not pending:
        return

    changed = False
    for pending_key, item in list(pending.items()):
        identity_id = int((item or {}).get("identity_id", 0) or 0)
        target = str((item or {}).get("target") or "未知目标")
        answer = str((item or {}).get("answer") or "").strip().upper()
        msg_id = int((item or {}).get("msg_id", 0) or 0)
        due_at = float((item or {}).get("due_at", 0) or 0)
        deadline_at = float((item or {}).get("deadline_at", 0) or 0)

        if not answer or msg_id <= 0 or (deadline_at > 0 and now >= deadline_at):
            pending.pop(pending_key, None)
            changed = True
            if answer and msg_id > 0:
                await send_audit_log(f"🧭 天机考验作答已超时：{mono(target)}", scope="global", limit=260)
            continue
        if due_at <= 0 or now < due_at:
            continue
        if identity_id <= 0 or identity_id not in get_identity_ids():
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"🧭 天机考验未发送：{mono(target)} 身份不存在", scope="global", limit=260)
            continue

        msg = await send_game_command(answer, track=False, reply_to=msg_id, send_as_id=identity_id)
        if msg:
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"🧭 天机考验已作答：{mono(target)}｜{answer}", scope="global", limit=260)
            continue

        retry_count = int((item or {}).get("retry_count", 0) or 0) + 1
        item["retry_count"] = retry_count
        item["due_at"] = min(now + TIANJI_QUIZ_RETRY_DELAY_SEC, max(now + 1, deadline_at - 1)) if deadline_at > now + 1 else now + 1
        pending[pending_key] = item
        changed = True
        if retry_count == 1:
            await send_audit_log(f"🧭 天机考验作答发送失败，稍后重试：{mono(target)}", scope="global", limit=260)

    if changed:
        _set_tianji_quiz_pending_map(pending)


__all__ = [
    "handle_tianji_quiz_prompt",
    "parse_tianji_quiz_prompt",
    "run_tianji_quiz_scheduler",
    "save_tianji_quiz_bank_entry",
]
