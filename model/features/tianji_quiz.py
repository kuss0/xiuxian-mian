import json
import os
import re

from ..config import TIANJI_QUIZ_BANK_FILE
from ..runtime import mono, send_audit_log


TIANJI_QUIZ_PROMPT_KEYWORDS = ("【天机考验】", "直接回复本消息", "回答错误或超时")
TIANJI_QUIZ_OPTIONS = ("A", "B", "C", "D")
RE_TIANJI_TARGET = re.compile(r"@([^\s，。！？、；：:,.!?\]）】()（）【\[\]<>《》“”\"'`]+)")
RE_TIANJI_OPTION = re.compile(r"^\s*([A-D])\.\s*(.+?)\s*$", re.M)
RE_TIANJI_WHITESPACE = re.compile(r"\s+")


def _is_tianji_quiz_prompt(text):
    raw_text = str(text or "")
    return all(keyword in raw_text for keyword in TIANJI_QUIZ_PROMPT_KEYWORDS)


def _normalize_text(text):
    return RE_TIANJI_WHITESPACE.sub("", str(text or "")).strip().lower()


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


async def handle_tianji_quiz_prompt(text, now=None, event=None):
    parsed = parse_tianji_quiz_prompt(text)
    if not parsed:
        return False

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
    return True


__all__ = [
    "handle_tianji_quiz_prompt",
    "parse_tianji_quiz_prompt",
    "save_tianji_quiz_bank_entry",
]
