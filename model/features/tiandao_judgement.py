import asyncio
import ast
import json
import operator
import os
import random
import re
import time

from ..config import CMD_TIANDAO_JUDGEMENT_PROVE, MESSAGES_DIR
from ..persistence import save_state
from ..runtime import _fire_and_forget, _get_identity_client, console_log, get_reply_context, mono, send_audit_log, send_game_command
from ..state import (
    get_current_identity_id,
    get_identity_account,
    get_identity_display_name,
    get_identity_ids,
    get_send_as_tags,
    set_identity_enabled,
    state,
    use_identity,
)
from ..timing import fmt_time_after
from .tiandao_miniapp import (
    extract_tiandao_miniapp_challenge,
    run_tiandao_miniapp_drag_verification,
    sanitize_tiandao_miniapp_error,
    summarize_tiandao_miniapp_token,
)


TIANDAO_JUDGEMENT_PROMPT_MARKERS = ("天道审判", "天道问心", "挂机嫌疑")
TIANDAO_JUDGEMENT_PROOF_KEYWORDS = ("自证",)
TIANDAO_JUDGEMENT_RETRY_DELAY_SEC = 10
TIANDAO_JUDGEMENT_MAX_RETRY_COUNT = 1
TIANDAO_JUDGEMENT_DEADLINE_BUFFER_SEC = 5
TIANDAO_JUDGEMENT_DELAY_MIN_SEC = 40
TIANDAO_JUDGEMENT_DELAY_MAX_SEC = 60
TIANDAO_JUDGEMENT_DEFAULT_TIMEOUT_SEC = 3 * 60
TIANDAO_JUDGEMENT_BUTTON_CLICK_DELAY_MIN_SEC = 0.7
TIANDAO_JUDGEMENT_BUTTON_CLICK_DELAY_MAX_SEC = 1.6
TIANDAO_MINIAPP_DELAY_MIN_SEC = 3
TIANDAO_MINIAPP_DELAY_MAX_SEC = 8
TIANDAO_MINIAPP_RETRY_LIMIT = 1
TIANDAO_MINIAPP_TERMINAL_TTL_SEC = 30 * 60
_TIANDAO_JUDGEMENT_SCHEDULER_LOCK = asyncio.Lock()
_tiandao_miniapp_terminal_events = {}

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
    "股市买入基础手续费": 2,
}

RE_TIANDAO_TARGET = re.compile(r"对象\s*(?:@[^\s，。！？、；：:,.!?()（）【】\[\]]+\s*)?[（(]?[【\[]\s*([^】\]]+?)\s*[】\]][）)]?")
RE_TIANDAO_TOKEN = re.compile(r"阵眼口令\s*[:：]\s*(?P<token>\S+)")
RE_TIANDAO_VERIFY_COMMAND = re.compile(r"(?:回复指令|直接发送指令|发送指令|发送)\s*[:：]\s*(?P<command>\.\S+)")
RE_TIANDAO_TIMEOUT_MIN = re.compile(r"(\d+)\s*分钟")
RE_TIANDAO_TIMEOUT_SEC = re.compile(r"(\d+)\s*秒")
RE_TIANDAO_BUTTON_SEQUENCE = re.compile(r"点击顺序\s*[:：]\s*(?P<sequence>.+?)(?:\n|$)")
RE_TIANDAO_QUESTION = re.compile(
    r"(?:请计算图中结果|文本题面|天道问心|速答|长老考校|请问|请直接计算)?\s*[:：]?\s*(?:✨\s*)?"
    r"(?:请直接计算\s*[:：]\s*)?(?:敢问)?\s*"
    r"(?P<left>[^\n✨?？=]+?)\s*(?P<op>加|减|乘|除|[+＋\-−×*÷/])\s*"
    r"(?P<right>[零〇一二两三四五六七八九十百千万萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟\d０-９]+)\s*(?:等于\s*)?(?:=\s*)?[?？]",
    re.S,
)
RE_TIANDAO_MOD_QUESTION = re.compile(
    r"(?:文本题面\s*[:：]\s*)?"
    r"(?:请直接计算\s*[:：]\s*)?"
    r"(?:计算\s*[:：]\s*)?"
    r"(?P<expr>[^\n=？?]+?)\s*除以\s*"
    r"(?P<mod>[零〇一二两三四五六七八九十百千万萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟\d０-９]+)\s*的余数",
    re.S,
)
RE_TIANDAO_ARITHMETIC_QUESTION = re.compile(
    r"(?:文本题面\s*[:：]\s*)?"
    r"(?:(?:请直接计算|计算)\s*[:：]\s*)+"
    r"(?P<expr>[\d０-９\s+＋\-−×＊*/÷／%().（）]+)\s*(?:=\s*)?[?？]",
    re.S,
)
RE_IDENTITY_SEPARATORS = re.compile(r"[\s@，。！？、；：:,.!?\[\]【】()（）<>《》“”\"'`]+")
RE_QUESTION_WHITESPACE = re.compile(r"\s+")
RE_SAFE_ARITHMETIC_CHARS = re.compile(r"^[\d\s+\-*/%().]+$")

SAFE_ARITHMETIC_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.floordiv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
SAFE_ARITHMETIC_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

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
    raw_lower = raw_text.lower()
    if "随机阵列验证" in raw_text and "点击顺序" in raw_text:
        return True
    if (
        "startapp=rpt_" in raw_lower
        or "startapp=stk_" in raw_lower
        or "start_param=rpt_" in raw_lower
        or "start_param=stk_" in raw_lower
    ):
        return True
    if "Mini App 拖动验证" in raw_text and (
        "天道审判" in raw_text
        or "本轮挑战码" in raw_text
        or "天道迷障" in raw_text
        or "神识验证" in raw_text
        or "打开验证" in raw_text
    ):
        return True
    if "阵眼口令" in raw_text and ("自证方式" in raw_text or "请直接计算" in raw_text):
        return True
    if "请计算图中结果" in raw_text and "自证" in raw_text:
        return True
    if "文本题面" in raw_text and "请直接计算" in raw_text and "自证" in raw_text:
        return True
    if "天道问心" in raw_text and "回复指令" in raw_text:
        return True
    return (
        any(keyword in raw_text for keyword in TIANDAO_JUDGEMENT_PROMPT_MARKERS)
        and any(keyword in raw_text for keyword in TIANDAO_JUDGEMENT_PROOF_KEYWORDS)
    )


def _is_tiandao_judgement_punishment(text):
    raw_text = str(text or "")
    return "天道裁决" in raw_text and ("挂机傀儡" in raw_text or "死牢" in raw_text)


def _is_tiandao_judgement_success(text):
    raw_text = str(text or "")
    return (
        ("Mini App 验证完成" in raw_text and "已通过本轮交易验证" in raw_text)
        or ("迷障破除" in raw_text and "已自证清白" in raw_text)
        or ("天道裁决" in raw_text and "真相大白" in raw_text and "已完成本轮自证" in raw_text)
    )


def _normalize_question_left(text):
    normalized = RE_QUESTION_WHITESPACE.sub("", str(text or "")).strip("✨:： ")
    changed = True
    while changed:
        changed = False
        for prefix in ("请计算图中结果", "文本题面", "天道问心", "速答", "长老考校", "请问", "请直接计算", "敢问"):
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip("✨:： ")
                changed = True
                break
    return normalized


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


def _normalize_arithmetic_expr(text):
    return (
        str(text or "")
        .strip()
        .translate(FULL_WIDTH_DIGITS)
        .replace("＋", "+")
        .replace("−", "-")
        .replace("×", "*")
        .replace("＊", "*")
        .replace("x", "*")
        .replace("X", "*")
        .replace("÷", "/")
        .replace("／", "/")
        .replace("（", "(")
        .replace("）", ")")
    )


def _safe_eval_arithmetic_node(node):
    if isinstance(node, ast.Expression):
        return _safe_eval_arithmetic_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return int(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_ARITHMETIC_UNARY_OPERATORS:
        return SAFE_ARITHMETIC_UNARY_OPERATORS[type(node.op)](_safe_eval_arithmetic_node(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in SAFE_ARITHMETIC_OPERATORS:
        left_value = _safe_eval_arithmetic_node(node.left)
        right_value = _safe_eval_arithmetic_node(node.right)
        if right_value == 0 and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
            raise ValueError("division by zero")
        return SAFE_ARITHMETIC_OPERATORS[type(node.op)](left_value, right_value)
    raise ValueError("unsupported arithmetic expression")


def _safe_eval_arithmetic_expr(text):
    expr = _normalize_arithmetic_expr(text)
    if not expr or not RE_SAFE_ARITHMETIC_CHARS.match(expr):
        return None
    try:
        return int(_safe_eval_arithmetic_node(ast.parse(expr, mode="eval")))
    except Exception:
        return None


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


def _format_judgement_detail(target, question, answer):
    return (
        f"{mono(target or '未知对象')}"
        f"｜题目：{str(question or '未知题目').strip()}"
        f"｜答案：{str(answer or '未计算').strip()}"
    )


def _calculate_answer(left_value, op, right_value):
    if op in {"加", "+", "＋"}:
        return _format_answer(left_value + right_value)
    if op in {"减", "-", "−"}:
        return _format_answer(left_value - right_value)
    if op in {"乘", "×", "*"}:
        return _format_answer(left_value * right_value)
    if op in {"除", "÷", "/"}:
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
    target_match = RE_TIANDAO_TARGET.search(raw_text)
    target = str(target_match.group(1) or "").strip() if target_match else ""
    token_match = RE_TIANDAO_TOKEN.search(raw_text)
    token = str(token_match.group("token") or "").strip() if token_match else ""
    command_match = RE_TIANDAO_VERIFY_COMMAND.search(raw_text)
    command = str(command_match.group("command") or "").strip() if command_match else CMD_TIANDAO_JUDGEMENT_PROVE

    mod_match = RE_TIANDAO_MOD_QUESTION.search(raw_text)
    if mod_match:
        expr_text = _normalize_arithmetic_expr(mod_match.group("expr"))
        mod_text = str(mod_match.group("mod") or "").strip()
        return {
            "kind": "mod",
            "target": target,
            "token": token,
            "command": command or CMD_TIANDAO_JUDGEMENT_PROVE,
            "question": f"{expr_text} mod {mod_text}",
            "expr_text": expr_text,
            "mod_text": mod_text,
            "timeout_sec": _parse_timeout_sec(raw_text),
        }

    arithmetic_match = RE_TIANDAO_ARITHMETIC_QUESTION.search(raw_text)
    if arithmetic_match:
        expr_text = _normalize_arithmetic_expr(arithmetic_match.group("expr"))
        return {
            "kind": "arithmetic",
            "target": target,
            "token": token,
            "command": command or CMD_TIANDAO_JUDGEMENT_PROVE,
            "question": expr_text,
            "expr_text": expr_text,
            "timeout_sec": _parse_timeout_sec(raw_text),
        }

    question_match = RE_TIANDAO_QUESTION.search(raw_text)
    if not question_match:
        return None

    left_text = _normalize_question_left(question_match.group("left"))
    right_text = str(question_match.group("right") or "").strip()
    op = str(question_match.group("op") or "").strip()
    return {
        "kind": "knowledge",
        "target": target,
        "token": token,
        "command": command or CMD_TIANDAO_JUDGEMENT_PROVE,
        "question": f"{left_text}{op}{right_text}",
        "left_text": left_text,
        "op": op,
        "right_text": right_text,
        "timeout_sec": _parse_timeout_sec(raw_text),
    }


def _complete_tiandao_judgement_question(question):
    if not question:
        return None

    parsed = dict(question)
    if parsed.get("kind") == "mod":
        expr_value = _safe_eval_arithmetic_expr(parsed.get("expr_text"))
        mod_value = _parse_chinese_integer(parsed.get("mod_text"))
        if expr_value is None or mod_value is None or mod_value == 0:
            return None
        parsed.update({
            "expr_value": expr_value,
            "mod_value": mod_value,
            "answer": str(expr_value % mod_value),
        })
        return parsed

    if parsed.get("kind") == "arithmetic":
        expr_value = _safe_eval_arithmetic_expr(parsed.get("expr_text"))
        if expr_value is None:
            return None
        parsed.update({
            "expr_value": expr_value,
            "answer": _format_answer(expr_value),
        })
        return parsed

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


def parse_tiandao_judgement_prompt(text):
    return _complete_tiandao_judgement_question(_extract_tiandao_judgement_question(text))


def _extract_tiandao_button_sequence(text):
    if not _is_tiandao_judgement_prompt(text):
        return None
    raw_text = str(text or "")
    if "随机阵列验证" not in raw_text:
        return None
    match = RE_TIANDAO_BUTTON_SEQUENCE.search(raw_text)
    if not match:
        return None
    sequence = [part.strip() for part in re.split(r"\s*→\s*", match.group("sequence")) if part.strip()]
    if not sequence:
        return None
    target_match = RE_TIANDAO_TARGET.search(raw_text)
    target = str(target_match.group(1) or "").strip() if target_match else ""
    return {
        "target": target,
        "sequence": sequence,
        "timeout_sec": _parse_timeout_sec(raw_text),
    }


def _get_event_message(event):
    return getattr(event, "message", None) or event


def _normalize_button_text(text):
    return str(text or "").strip()


async def _click_tiandao_judgement_buttons(event, identity_id, sequence):
    message = _get_event_message(event)
    if message is None:
        return False, "消息对象为空"
    buttons = getattr(message, "buttons", None) or []
    if not buttons:
        return False, "消息没有按钮"

    available = []
    for row_index, row in enumerate(buttons):
        for col_index, button in enumerate(row or []):
            text = _normalize_button_text(getattr(button, "text", ""))
            if text:
                available.append((text, row_index, col_index))

    click_positions = []
    used_positions = set()
    for expected_text in sequence:
        expected_text = _normalize_button_text(expected_text)
        matched_position = None
        for text, row_index, col_index in available:
            position = (row_index, col_index)
            if position in used_positions:
                continue
            if text == expected_text:
                matched_position = position
                break
        if matched_position is None:
            return False, f"未找到按钮：{expected_text}"
        used_positions.add(matched_position)
        click_positions.append(matched_position)

    client = _get_identity_client(identity_id)
    if client is None:
        return False, "身份客户端不可用"
    message_id = int(getattr(message, "id", 0) or 0)
    chat_id = getattr(message, "chat_id", None) or getattr(event, "chat_id", None)
    if message_id > 0 and chat_id:
        message = await client.get_messages(chat_id, ids=message_id)
        if message is None:
            return False, f"无法重新获取消息：{message_id}"

    for row_index, col_index in click_positions:
        await message.click(row_index, col_index)
        await asyncio.sleep(random.uniform(TIANDAO_JUDGEMENT_BUTTON_CLICK_DELAY_MIN_SEC, TIANDAO_JUDGEMENT_BUTTON_CLICK_DELAY_MAX_SEC))
    return True, ""


async def _handle_tiandao_button_sequence_prompt(text, now, event):
    parsed = _extract_tiandao_button_sequence(text)
    if not parsed:
        return False

    target = parsed.get("target") or "被回复身份"
    identity_id = await _resolve_tiandao_identity_id(parsed.get("target"), event)
    if identity_id is None or int(identity_id or 0) <= 0:
        await send_audit_log(f"⚖️ 天道阵列验证未匹配身份：{mono(target)}", scope="global", limit=260)
        return True

    ok, error = await _click_tiandao_judgement_buttons(event, identity_id, parsed["sequence"])
    if not ok:
        await send_audit_log(f"⚖️ 天道阵列验证点击失败：{mono(target)}｜{mono(error)}", scope="global", limit=320)
        return True

    await send_audit_log(
        f"⚖️ 天道阵列验证已点击：{mono(target)}｜{' → '.join(parsed['sequence'])}",
        scope="global",
        limit=360,
    )
    return True


def _build_miniapp_pending_item(parsed, identity_id, event, now):
    delay_sec = random.uniform(TIANDAO_MINIAPP_DELAY_MIN_SEC, TIANDAO_MINIAPP_DELAY_MAX_SEC)
    timeout_sec = float(parsed.get("timeout_sec") or TIANDAO_JUDGEMENT_DEFAULT_TIMEOUT_SEC)
    deadline_at = float(now) + timeout_sec
    due_at = min(float(now) + delay_sec, max(float(now) + 1, deadline_at - TIANDAO_JUDGEMENT_DEADLINE_BUFFER_SEC))
    return {
        "kind": "miniapp_drag",
        "miniapp_kind": parsed.get("kind") or "",
        "target": parsed.get("target") or "",
        "identity_id": int(identity_id),
        "token": parsed.get("token") or "",
        "due_at": float(due_at),
        "deadline_at": float(deadline_at),
        "created_at": float(now),
        "msg_id": int(getattr(event, "id", 0) or 0),
        "chat_id": int(getattr(event, "chat_id", 0) or 0),
        "retry_count": 0,
    }


def _gc_tiandao_miniapp_terminal_events(now=None):
    now = float(now if now is not None else time.time())
    expired_keys = [key for key, expires_at in _tiandao_miniapp_terminal_events.items() if float(expires_at or 0) <= now]
    for key in expired_keys:
        _tiandao_miniapp_terminal_events.pop(key, None)


def _get_miniapp_terminal_key(pending_key, token):
    pending_key = str(pending_key or "").strip()
    token = str(token or "").strip()
    if not pending_key or not token:
        return ""
    return f"{pending_key}:{token}"


def _is_miniapp_terminal_event(terminal_key, now=None):
    terminal_key = str(terminal_key or "").strip()
    if not terminal_key:
        return False
    now = float(now if now is not None else time.time())
    _gc_tiandao_miniapp_terminal_events(now)
    return float(_tiandao_miniapp_terminal_events.get(terminal_key, 0) or 0) > now


def _mark_miniapp_terminal_event(terminal_key, now=None):
    terminal_key = str(terminal_key or "").strip()
    if not terminal_key:
        return
    now = float(now if now is not None else time.time())
    _gc_tiandao_miniapp_terminal_events(now)
    _tiandao_miniapp_terminal_events[terminal_key] = now + TIANDAO_MINIAPP_TERMINAL_TTL_SEC


async def _handle_tiandao_miniapp_prompt(text, now, event):
    parsed = extract_tiandao_miniapp_challenge(text, event, timeout_sec=_parse_timeout_sec(text))
    if not parsed:
        return False

    pending_key = _get_event_pending_key(
        event,
        {
            "target": parsed.get("target"),
            "question": "miniapp",
            "answer": parsed.get("token"),
        },
    )
    terminal_key = _get_miniapp_terminal_key(pending_key, parsed.get("token"))
    if _is_miniapp_terminal_event(terminal_key, now):
        return True

    target = parsed.get("target") or "被回复身份"
    identity_context = await _resolve_tiandao_identity_context(parsed.get("target"), event)
    identity_id = int((identity_context or {}).get("identity_id") or 0)
    if identity_id is None or int(identity_id or 0) <= 0:
        _mark_miniapp_terminal_event(terminal_key, now)
        external_sender_id = int((identity_context or {}).get("external_sender_id") or 0)
        matched_via = str((identity_context or {}).get("matched_via") or "")
        if external_sender_id:
            console_log(
                f"⚖️ 天道 Mini App 外部身份验证，跳过：sender={external_sender_id}｜{parsed.get('kind') or ''}",
                scope="global",
                limit=220,
            )
        else:
            await send_audit_log(
                f"⚖️ 天道 Mini App 验证未匹配本地身份：{mono(target)}｜{mono(parsed.get('kind') or '')}｜{mono(matched_via)}",
                scope="global",
                limit=320,
            )
        return True

    pending = _get_pending_map()
    if pending_key in pending:
        return True

    item = _build_miniapp_pending_item(parsed, identity_id, event, now)
    item["terminal_key"] = terminal_key
    pending[pending_key] = item
    _set_pending_map(pending)
    _schedule_tiandao_judgement_due_task(item["due_at"])
    console_log(
        f"⚖️ 天道 Mini App 验证排队：{target}｜{parsed.get('kind')}｜{fmt_time_after(item['due_at'] - now)}后",
        scope="global",
    )
    return True


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


def _get_identity_tag_keys(identity_id):
    return {
        _normalize_identity_text(tag)
        for tag in get_send_as_tags(identity_id)
        if _normalize_identity_text(tag)
    }


def _find_target_identity_id(target):
    target_key = _normalize_identity_text(target)
    if not target_key:
        return None
    matched_ids = []
    for identity_id in get_identity_ids():
        if target_key in _get_identity_tag_keys(identity_id):
            matched_ids.append(int(identity_id))
    if len(matched_ids) == 1:
        return matched_ids[0]
    return None


def _format_tiandao_identity_detail(identity_id):
    tags = get_send_as_tags(identity_id)
    primary_tag = tags[0] if tags else f"@{identity_id}"
    return f"{primary_tag}｜{get_identity_display_name(identity_id)}"


def _format_tiandao_external_target(target):
    target = str(target or "").strip()
    if not target:
        return "未知对象"
    return target if target.startswith("@") else f"@{target}"


def _get_event_reply_header_msg_id(event):
    reply_header = getattr(event, "reply_to", None)
    return int(getattr(reply_header, "reply_to_msg_id", 0) or 0)


def _message_log_names(limit=3):
    try:
        names = [
            name
            for name in os.listdir(MESSAGES_DIR)
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.log", str(name or ""))
        ]
    except OSError:
        return []
    return sorted(names, reverse=True)[: max(1, int(limit or 3))]


def _find_message_log_entry_by_msg_id(msg_id, *, chat_id=0, limit_files=3):
    msg_id = int(msg_id or 0)
    chat_id = int(chat_id or 0)
    if msg_id <= 0:
        return None
    needle = str(msg_id)
    for name in _message_log_names(limit_files):
        path = os.path.join(MESSAGES_DIR, name)
        try:
            with open(path, "r", encoding="utf-8") as fp:
                for raw_line in fp:
                    if needle not in raw_line:
                        continue
                    try:
                        payload = json.loads(raw_line)
                    except ValueError:
                        continue
                    if int((payload or {}).get("message_id", 0) or 0) != msg_id:
                        continue
                    if chat_id and int((payload or {}).get("chat_id", 0) or 0) != chat_id:
                        continue
                    return payload
        except OSError:
            continue
    return None


def _normalize_sender_candidates(sender_id):
    try:
        sender_id = int(sender_id or 0)
    except (TypeError, ValueError):
        return []
    if sender_id == 0:
        return []

    candidates = [sender_id]
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            try:
                candidates.append(int(sender_abs[3:]))
            except ValueError:
                pass
    return candidates


def _find_identity_id_by_sender_id(sender_id):
    candidates = _normalize_sender_candidates(sender_id)
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        for candidate in candidates:
            if candidate == identity_id or candidate == int(get_identity_account(identity_id) or 0):
                return identity_id
    return None


def _find_identity_id_by_message_sender(message):
    return _find_identity_id_by_sender_id(getattr(message, "sender_id", 0) if message is not None else 0)


def _first_sender_candidate(sender_id):
    candidates = _normalize_sender_candidates(sender_id)
    return int(candidates[-1]) if candidates else 0


async def _find_reply_identity_context(event):
    if event is None:
        return {"identity_id": None, "matched_via": "no_event", "external_sender_id": 0}
    reply_header_msg_id = _get_event_reply_header_msg_id(event)
    try:
        reply_to = await event.get_reply_message()
    except Exception:
        reply_to = None
    reply_sender_id = int(getattr(reply_to, "sender_id", 0) or 0) if reply_to is not None else 0
    identity_id = _find_identity_id_by_sender_id(reply_sender_id)
    if identity_id is not None:
        return {"identity_id": identity_id, "matched_via": "reply_sender", "external_sender_id": 0}
    reply_context = get_reply_context(reply_to, reply_to_msg_id=reply_header_msg_id)
    identity_id = int((reply_context or {}).get("send_as_id") or 0)
    if identity_id > 0:
        return {"identity_id": identity_id, "matched_via": (reply_context or {}).get("matched_via") or "reply_context", "external_sender_id": 0}

    logged_reply = _find_message_log_entry_by_msg_id(reply_header_msg_id, chat_id=getattr(event, "chat_id", 0))
    logged_sender_id = int((logged_reply or {}).get("sender_id", 0) or 0)
    identity_id = _find_identity_id_by_sender_id(logged_sender_id)
    if identity_id is not None:
        return {"identity_id": identity_id, "matched_via": "message_log_sender", "external_sender_id": 0}

    external_sender_id = _first_sender_candidate(logged_sender_id or reply_sender_id)
    matched_via = "message_log_external_sender" if logged_sender_id else ("reply_external_sender" if reply_sender_id else "none")
    return {"identity_id": None, "matched_via": matched_via, "external_sender_id": external_sender_id}


async def _find_reply_identity_id(event):
    context = await _find_reply_identity_context(event)
    identity_id = int((context or {}).get("identity_id") or 0)
    return identity_id if identity_id > 0 else None


async def _resolve_tiandao_identity_context(target, event):
    identity_id = _find_target_identity_id(target)
    if identity_id is not None:
        return {"identity_id": identity_id, "matched_via": "target", "external_sender_id": 0}
    if _normalize_identity_text(target):
        return {"identity_id": None, "matched_via": "target_unmatched", "external_sender_id": 0}
    return await _find_reply_identity_context(event)


async def _resolve_tiandao_identity_id(target, event):
    context = await _resolve_tiandao_identity_context(target, event)
    identity_id = int((context or {}).get("identity_id") or 0)
    return identity_id if identity_id > 0 else None


def _remove_identity_pending(identity_id):
    identity_id = int(identity_id or 0)
    pending = _get_pending_map()
    changed = False
    for pending_key, item in list(pending.items()):
        if int((item or {}).get("identity_id", 0) or 0) == identity_id:
            pending.pop(pending_key, None)
            changed = True
    if changed:
        _set_pending_map(pending)


def _pending_item_matches_tiandao_success(item, *, reply_to_msg_id=0, target=""):
    if int(reply_to_msg_id or 0) > 0 and int((item or {}).get("msg_id", 0) or 0) == int(reply_to_msg_id or 0):
        return True
    target_key = _normalize_identity_text(target)
    if not target_key:
        return False
    if _normalize_identity_text((item or {}).get("target")) == target_key:
        return True
    identity_id = int((item or {}).get("identity_id", 0) or 0)
    return identity_id > 0 and target_key in _get_identity_tag_keys(identity_id)


def _clear_tiandao_pending_for_success(text, event=None, now=None):
    pending = _get_pending_map()
    if not pending:
        return False
    target_match = RE_TIANDAO_TARGET.search(str(text or ""))
    target = str(target_match.group(1) or "").strip() if target_match else ""
    reply_to_msg_id = _get_event_reply_header_msg_id(event)
    changed = False
    cleared_targets = []
    for pending_key, item in list(pending.items()):
        if not _pending_item_matches_tiandao_success(item, reply_to_msg_id=reply_to_msg_id, target=target):
            continue
        pending.pop(pending_key, None)
        changed = True
        cleared_targets.append(str((item or {}).get("target") or target or "未知对象"))
        if str((item or {}).get("kind") or "") == "miniapp_drag":
            token = str((item or {}).get("token") or "").strip()
            terminal_key = str((item or {}).get("terminal_key") or _get_miniapp_terminal_key(pending_key, token))
            _mark_miniapp_terminal_event(terminal_key, now)
    if changed:
        _set_pending_map(pending)
        console_log(
            f"⚖️ 天道验证成功回执，已清理 pending：{', '.join(cleared_targets[:3])}",
            scope="global",
        )
    return changed


async def handle_tiandao_judgement_punishment(text, now, event=None):
    if _is_tiandao_judgement_success(text):
        _clear_tiandao_pending_for_success(text, event=event, now=now)
        return True

    if not _is_tiandao_judgement_punishment(text):
        return False

    target_match = RE_TIANDAO_TARGET.search(str(text or ""))
    target = str(target_match.group(1) or "").strip() if target_match else ""
    identity_id = _find_target_identity_id(target)
    if identity_id is None:
        await send_audit_log(
            f"⚖️ 天道裁决外部对象：{mono(_format_tiandao_external_target(target))}｜未匹配本地身份，已忽略停用。",
            scope="global",
            limit=420,
            priority="normal",
        )
        return True

    set_identity_enabled(identity_id, False)
    with use_identity(identity_id):
        state["pending_tasks"] = {}
    _remove_identity_pending(identity_id)
    save_state()
    identity_detail = _format_tiandao_identity_detail(identity_id)
    await send_audit_log(
        f"⚖️ 天道裁决命中本地身份：{mono(identity_detail)}｜对象：{mono(target)}｜已自动停用该身份并清空待发任务。",
        scope="global",
        limit=520,
        priority="high",
    )
    return True


async def _send_tiandao_judgement_parse_failure_log(text, question=None):
    if question is None:
        question = _extract_tiandao_judgement_question(text)
    if question:
        left_text = question.get("left_text") or ""
        question_text = question.get("question") or ""
        if question.get("kind") == "mod":
            await send_audit_log(f"⚖️ 天道审判算术题解析失败：{mono(question_text or '未知题目')}", scope="global", limit=360)
            return
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
        "token": parsed.get("token") or "",
        "command": parsed.get("command") or CMD_TIANDAO_JUDGEMENT_PROVE,
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


async def _run_tiandao_judgement_due_task(due_at):
    await asyncio.sleep(max(0.0, float(due_at or 0) - time.time()))
    await run_tiandao_judgement_scheduler(time.time())


def _schedule_tiandao_judgement_due_task(due_at):
    due_at = float(due_at or 0)
    if due_at > 0:
        _fire_and_forget(_run_tiandao_judgement_due_task(due_at))


async def handle_tiandao_judgement_prompt(text, now, event=None):
    if not state.get("tiandao_judgement_enabled"):
        return False
    if not _is_tiandao_judgement_prompt(text):
        return False

    if event is not None and await _handle_tiandao_button_sequence_prompt(text, now, event):
        return True

    if event is not None and await _handle_tiandao_miniapp_prompt(text, now, event):
        return True

    question = _extract_tiandao_judgement_question(text)
    if not question:
        await _send_tiandao_judgement_parse_failure_log(text)
        return True

    parsed = _complete_tiandao_judgement_question(question)
    if not parsed:
        await _send_tiandao_judgement_parse_failure_log(text, question)
        return True

    identity_id = await _resolve_tiandao_identity_id(question.get("target"), event)
    if identity_id is None:
        await send_audit_log(
            f"⚖️ 天道审判未匹配身份：{mono(question.get('target') or '未知对象')}｜题目：{question.get('question') or '未知题目'}",
            scope="global",
            limit=420,
        )
        return True

    pending_key = _get_event_pending_key(event, parsed)
    pending = _get_pending_map()
    if pending_key in pending:
        return True

    item = _build_pending_item(parsed, identity_id, event, now)
    pending[pending_key] = item
    _set_pending_map(pending)
    _schedule_tiandao_judgement_due_task(item["due_at"])
    console_log(
        f"⚖️ 天道审判排队：{parsed['target']}｜题目 {parsed['question']}｜答案 {parsed['answer']}｜{fmt_time_after(item['due_at'] - now)}后",
        scope="global",
    )
    return True


async def run_tiandao_judgement_scheduler(now):
    async with _TIANDAO_JUDGEMENT_SCHEDULER_LOCK:
        await _run_tiandao_judgement_scheduler_locked(now)


async def _run_tiandao_judgement_scheduler_locked(now):
    if not state.get("tiandao_judgement_enabled"):
        return

    pending = _get_pending_map()
    if not pending:
        return

    changed = False
    for pending_key, item in list(pending.items()):
        item_kind = str((item or {}).get("kind") or "")
        identity_id = int((item or {}).get("identity_id", 0) or 0)
        target = str((item or {}).get("target") or "未知对象")
        token = str((item or {}).get("token") or "").strip()
        answer = str((item or {}).get("answer") or "").strip()
        question = str((item or {}).get("question") or "未知题目")
        detail = _format_judgement_detail(target, question, answer)
        due_at = float((item or {}).get("due_at", 0) or 0)
        deadline_at = float((item or {}).get("deadline_at", 0) or 0)

        if item_kind == "miniapp_drag":
            terminal_key = str((item or {}).get("terminal_key") or _get_miniapp_terminal_key(pending_key, token))
            if _is_miniapp_terminal_event(terminal_key, now):
                pending.pop(pending_key, None)
                changed = True
                continue
            if not token or (deadline_at > 0 and now >= deadline_at):
                pending.pop(pending_key, None)
                changed = True
                _mark_miniapp_terminal_event(terminal_key, now)
                await send_audit_log(f"⚖️ 天道 Mini App 验证已超时：{mono(target)}", scope="global", limit=280)
                continue
            if due_at <= 0 or now < due_at:
                continue
            if identity_id <= 0 or identity_id not in get_identity_ids():
                pending.pop(pending_key, None)
                changed = True
                _mark_miniapp_terminal_event(terminal_key, now)
                await send_audit_log(f"⚖️ 天道 Mini App 验证未提交：{mono(target)}｜身份不存在", scope="global", limit=300)
                continue

            result = await run_tiandao_miniapp_drag_verification(identity_id, token)
            if result.get("ok"):
                pending.pop(pending_key, None)
                changed = True
                _mark_miniapp_terminal_event(terminal_key, now)
                miniapp_kind = str((item or {}).get("miniapp_kind") or "")
                token_summary = summarize_tiandao_miniapp_token(token) or miniapp_kind
                await send_audit_log(
                    f"⚖️ 天道 Mini App 验证已提交：{mono(target)}｜{mono(token_summary)}",
                    scope="global",
                    limit=320,
                )
                continue

            retry_count = int((item or {}).get("retry_count", 0) or 0) + 1
            item["retry_count"] = retry_count
            if retry_count > TIANDAO_MINIAPP_RETRY_LIMIT:
                pending.pop(pending_key, None)
                changed = True
                _mark_miniapp_terminal_event(terminal_key, now)
                error = sanitize_tiandao_miniapp_error(result.get("error") or "未知错误")
                await send_audit_log(f"⚖️ 天道 Mini App 验证失败：{mono(target)}｜{mono(error)}", scope="global", limit=360)
                continue

            failed_at = time.time()
            item["due_at"] = (
                min(failed_at + TIANDAO_JUDGEMENT_RETRY_DELAY_SEC, max(failed_at + 1, deadline_at - 1))
                if deadline_at > failed_at + 1
                else failed_at + 1
            )
            pending[pending_key] = item
            _schedule_tiandao_judgement_due_task(item["due_at"])
            changed = True
            await send_audit_log(
                f"⚖️ 天道 Mini App 验证提交失败，稍后重试 {retry_count}/{TIANDAO_MINIAPP_RETRY_LIMIT}：{mono(target)}",
                scope="global",
                limit=320,
            )
            continue

        if not answer or (deadline_at > 0 and now >= deadline_at):
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"⚖️ 天道审判已超时：{detail}", scope="global", limit=520)
            continue
        if due_at <= 0 or now < due_at:
            continue
        if identity_id <= 0 or identity_id not in get_identity_ids():
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"⚖️ 天道审判未发送：{detail}｜身份不存在", scope="global", limit=520)
            continue

        command_prefix = str((item or {}).get("command") or CMD_TIANDAO_JUDGEMENT_PROVE).strip()
        if not command_prefix.startswith("."):
            command_prefix = CMD_TIANDAO_JUDGEMENT_PROVE
        command = f"{command_prefix} {token} {answer}" if token else f"{command_prefix} {answer}"
        reply_to_msg_id = int((item or {}).get("msg_id", 0) or 0)
        msg = await send_game_command(
            command,
            track=False,
            reply_to=reply_to_msg_id or None,
            send_as_id=identity_id,
            priority="p0",
        )
        if msg:
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(f"⚖️ 天道审判自证：{detail}｜{mono(command_prefix)}", scope="global", limit=520)
            continue

        retry_count = int((item or {}).get("retry_count", 0) or 0) + 1
        item["retry_count"] = retry_count
        if retry_count > TIANDAO_JUDGEMENT_MAX_RETRY_COUNT:
            pending.pop(pending_key, None)
            changed = True
            await send_audit_log(
                f"⚖️ 天道审判自证发送失败，已重试 {TIANDAO_JUDGEMENT_MAX_RETRY_COUNT} 次，停止重试：{detail}",
                scope="global",
                limit=520,
            )
            continue

        failed_at = time.time()
        item["due_at"] = (
            min(failed_at + TIANDAO_JUDGEMENT_RETRY_DELAY_SEC, max(failed_at + 1, deadline_at - 1))
            if deadline_at > failed_at + 1
            else failed_at + 1
        )
        pending[pending_key] = item
        _schedule_tiandao_judgement_due_task(item["due_at"])
        changed = True
        await send_audit_log(
            f"⚖️ 天道审判自证发送失败，稍后重试 {retry_count}/{TIANDAO_JUDGEMENT_MAX_RETRY_COUNT}：{detail}",
            scope="global",
            limit=520,
        )

    if changed:
        _set_pending_map(pending)


__all__ = [
    "handle_tiandao_judgement_punishment",
    "handle_tiandao_judgement_prompt",
    "parse_tiandao_judgement_prompt",
    "run_tiandao_judgement_scheduler",
]
