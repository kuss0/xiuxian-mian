import json
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime

from ..config import MESSAGES_DIR, STATE_DIR, TZ_LOCAL
from ..runtime import send_audit_log
from ..state import get_identity_ids, get_send_as_profile


REPORT_HOUR = 21
STATE_FILE = os.path.join(STATE_DIR, "rare_daily_report_state.json")

FOCUS_ITEMS = ("阴凝之晶", "昆吾通行令", "虚天残图", "苍坤残图")
FOCUS_DISPLAY_NAMES = {
    "阴凝之晶": "阴凝之晶",
    "昆吾通行令": "昆吾令",
    "虚天残图": "虚天残图",
    "苍坤残图": "苍坤残图",
}
FRAGMENT_ITEMS = {
    "虚天残图残纹": "虚天残纹",
    "苍坤残图残纹": "苍坤残纹",
}
OTHER_RARE_ITEMS = {
    "大挪移令",
    "一截灵眼之树",
    "万年灵乳",
    "丹魔心萃丹方",
    "九天息壤",
    "九天神雷木",
    "冰凤之翎",
    "古魔心核",
    "坠魔谷禁制令",
    "天凤之翎",
    "太虚仙露",
    "尘封的储物袋",
    "傀儡核心",
    "火凤之翎",
    "灵眼之树",
    "灵眼木髓碎片",
    "灵眼树胚",
    "皇鳞甲",
    "皇鳞甲图纸",
    "空间之核",
    "第二元神残篇",
    "赤炼金骨",
    "铁甲战傀图谱",
    "镇魔残篆",
    "雷鹏之羽",
    "鲲鹏之羽",
    "黄龙急援令",
    "雄黄残晶",
}
ITEM_ALIASES = {
    "昆吾令": "昆吾通行令",
}

RE_DREAM_FRAGMENT = re.compile(r"获得\s*【(?P<item>虚天残图|苍坤残图)】\s*残纹")
RE_BRACKET_COUNT = re.compile(r"【(?P<item>[^】]+)】\s*(?:[xX*＊]\s*(?P<count>[\d,]+))?")
RE_PLAIN_COUNT = re.compile(r"(?P<item>[\u4e00-\u9fffA-Za-z0-9·]+)\s*[xX*＊]\s*(?P<count>[\d,]+)")
RE_PUZZLE_GAIN = re.compile(r"你获得[:：]\s*(?P<body>.+?)(?:[。.\n]|$)")
RE_GENERIC_GAIN_BRACKET = re.compile(
    r"(?:获得(?:了|登顶至宝)?|额外获得(?:了)?|取出)\s*【(?P<item>[^】]+)】\s*(?:[xX*＊]\s*(?P<count>[\d,]+))?"
)
RE_EXCHANGE_ITEM = re.compile(r"【(?P<item>[^】]+)】已放入你的储物袋")

NEGATIVE_TEXT_MARKERS = (
    "物资统计",
    "持有明细",
    "奖励一览",
    "幸运掉落，权重",
    "赠送成功",
    "交易成功",
    "万宝楼",
    "归还至你的储物袋",
    "已将【",
    "暂无可用",
    "低优先级日志汇总",
    "监控日志",
)
POSITIVE_CONTEXT_MARKERS = (
    "换取成功",
    "乱星海远航·归",
    "元神归窍总结",
    "试炼古塔 - 战报",
    "野外历练",
    "入梦寻图",
    "拼合成功",
    "结算成果",
    "战利品结算",
    "登顶昆吾山",
    "挑战成功",
    "问道得宝",
)

_report_state_loaded = False
_report_state = {}
_last_sent_day_memory = ""
_next_retry_at = 0.0


@dataclass(frozen=True)
class RareMaterialEvent:
    item: str
    count: int
    source: str


def _parse_int(value, default=1):
    raw = str(value or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw.replace(",", ""))
    except (TypeError, ValueError):
        return int(default)


def _normalize_item_name(item):
    name = str(item or "").strip().strip("【】")
    name = re.sub(r"\s+", "", name)
    return ITEM_ALIASES.get(name, name)


def _is_rare_item(item):
    name = _normalize_item_name(item)
    if not name:
        return False
    if name in FOCUS_ITEMS or name in FRAGMENT_ITEMS or name in OTHER_RARE_ITEMS:
        return True
    if name.startswith("法则碎片"):
        return True
    if name.startswith("虚天鼎残片"):
        return True
    if name.startswith("元磁山核"):
        return True
    return False


def _add_event(events, item, count=1, source="未知"):
    name = _normalize_item_name(item)
    amount = _parse_int(count, default=1)
    if amount <= 0 or not _is_rare_item(name):
        return
    events.append(RareMaterialEvent(name, amount, source))


def _active_text(text):
    raw_text = str(text or "").strip()
    if "兜底命令" in raw_text:
        raw_text = raw_text.split("兜底命令", 1)[0].strip()
    return raw_text


def _is_negative_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return True
    if raw_text.startswith("."):
        return True
    if raw_text.lower().startswith("inventory "):
        return True
    if "法宝/丹药/杂物:" in raw_text or re.search(r"^@\S+\s+的储物袋", raw_text):
        return True
    if raw_text.startswith("【全群") or "全群异闻" in raw_text or "全群广播" in raw_text:
        return True
    if "消耗了【" in raw_text and "获得" not in raw_text:
        return True
    return any(marker in raw_text for marker in NEGATIVE_TEXT_MARKERS)


def _has_positive_context(text):
    raw_text = str(text or "")
    return any(marker in raw_text for marker in POSITIVE_CONTEXT_MARKERS)


def _parse_plain_counts(body, source):
    events = []
    for match in RE_PLAIN_COUNT.finditer(str(body or "")):
        _add_event(events, match.group("item"), match.group("count"), source)
    return events


def _parse_bracket_counts(body, source, *, require_count=True):
    events = []
    for match in RE_BRACKET_COUNT.finditer(str(body or "")):
        count_text = match.group("count")
        if require_count and not count_text:
            continue
        _add_event(events, match.group("item"), count_text or 1, source)
    return events


def _parse_tower_summary(text):
    if "试炼古塔 - 战报" not in text or "总收获:" not in text:
        return []
    summary = text.split("总收获:", 1)[1]
    for stop in ("\n\n本次塔相", "\n本次塔相", "\n\n同境界进度"):
        if stop in summary:
            summary = summary.split(stop, 1)[0]
    return _parse_bracket_counts(summary, "试炼古塔")


def _parse_voyage_return(text):
    if "乱星海远航·归" not in text:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("-"):
            continue
        events.extend(_parse_plain_counts(line.lstrip("-").strip(), "侍妾远航"))
    return events


def _parse_puzzle_success(text):
    if "拼合成功" not in text:
        return []
    events = []
    for match in RE_PUZZLE_GAIN.finditer(text):
        events.extend(_parse_plain_counts(match.group("body"), "残图拼合"))
    return events


def _parse_dream_fragment(text):
    if "入梦寻图" not in text:
        return []
    events = []
    for match in RE_DREAM_FRAGMENT.finditer(text):
        _add_event(events, f"{match.group('item')}残纹", 1, "入梦寻图")
    return events


def _parse_exchange_success(text):
    if "换取成功" not in text:
        return []
    events = []
    for match in RE_EXCHANGE_ITEM.finditer(text):
        _add_event(events, match.group("item"), 1, "卜筮换取")
    return events


def _parse_yuanying_summary(text):
    if "元神归窍总结" not in text:
        return []
    return _parse_bracket_counts(text, "元婴归窍")


def _parse_wild_training(text):
    if "野外历练" not in text:
        return []
    events = []
    for match in RE_GENERIC_GAIN_BRACKET.finditer(text):
        _add_event(events, match.group("item"), match.group("count") or 1, "野外历练")
    return events


def _parse_wendao(text):
    if "问道得宝" not in text:
        return []
    return _parse_bracket_counts(text, "问道得宝")


def _parse_settlement(text):
    if not any(marker in text for marker in ("结算成果", "战利品结算", "登顶昆吾山", "挑战成功")):
        return []
    events = []
    for match in RE_GENERIC_GAIN_BRACKET.finditer(text):
        _add_event(events, match.group("item"), match.group("count") or 1, "副本结算")
    return events


def parse_rare_material_events_from_text(text, event_type=""):
    raw_text = _active_text(text)
    if _is_negative_text(raw_text):
        return []
    if not _has_positive_context(raw_text):
        return []

    events = []
    events.extend(_parse_exchange_success(raw_text))
    events.extend(_parse_voyage_return(raw_text))
    events.extend(_parse_puzzle_success(raw_text))
    events.extend(_parse_dream_fragment(raw_text))
    events.extend(_parse_tower_summary(raw_text))
    events.extend(_parse_yuanying_summary(raw_text))
    events.extend(_parse_wild_training(raw_text))
    events.extend(_parse_wendao(raw_text))
    events.extend(_parse_settlement(raw_text))
    return events


def _daily_log_paths(day, messages_dir=None):
    base_dir = messages_dir or MESSAGES_DIR
    return [
        os.path.join(base_dir, f"{day}.log"),
        os.path.join(base_dir, f"replica-{day}.log"),
    ]


def _iter_daily_log_entries(day, messages_dir=None):
    latest = {}
    paths = _daily_log_paths(day, messages_dir)
    seq = 0
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                seq += 1
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg_id = int(payload.get("message_id") or 0)
                chat_id = int(payload.get("chat_id") or 0)
                key = (chat_id, msg_id) if msg_id else (chat_id, f"seq:{seq}")
                latest[key] = payload
    return list(latest.values()), [path for path in paths if os.path.exists(path)]


def _sender_identity_id(sender_id, identity_ids):
    try:
        sender_id = int(sender_id or 0)
    except (TypeError, ValueError):
        return 0
    if sender_id in identity_ids:
        return sender_id
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            try:
                candidate = int(sender_abs[3:])
            except ValueError:
                candidate = 0
            if candidate in identity_ids:
                return candidate
    return 0


def _build_identity_scope():
    identity_ids = set()
    usernames = set()
    for identity_id in get_identity_ids():
        try:
            identity_id = int(identity_id or 0)
        except (TypeError, ValueError):
            continue
        if identity_id <= 0:
            continue
        identity_ids.add(identity_id)
        profile = get_send_as_profile(identity_id) or {}
        username = str(profile.get("username") or "").strip().lstrip("@").lower()
        if username:
            usernames.add(username)
    return identity_ids, usernames


def _is_command_text(text):
    return str(text or "").strip().startswith(".")


def _build_own_command_keys(entries, identity_ids):
    keys = set()
    if not identity_ids:
        return keys
    for entry in entries:
        text = entry.get("text") or ""
        if not _is_command_text(text):
            continue
        sender_identity_id = _sender_identity_id(entry.get("sender_id"), identity_ids)
        if sender_identity_id <= 0:
            continue
        msg_id = int(entry.get("message_id") or 0)
        chat_id = int(entry.get("chat_id") or 0)
        if msg_id:
            keys.add((chat_id, msg_id))
    return keys


def _mentions_scope_username(text, usernames):
    if not usernames:
        return False
    lowered = str(text or "").lower()
    return any(f"@{username}" in lowered for username in usernames)


def _is_own_settlement_summary(entry):
    if str(entry.get("event_type") or "") != "sent":
        return False
    text = str(entry.get("text") or "")
    return any(marker in text for marker in ("结算成果", "昆吾山结算", "虚天殿结算", "苍坤", "落云秘圃结算", "坠魔谷结算"))


def _entry_matches_identity_scope(entry, identity_ids, usernames, own_command_keys):
    if not identity_ids and not usernames:
        return True
    text = entry.get("text") or ""
    if _sender_identity_id(entry.get("sender_id"), identity_ids) > 0:
        return True
    reply_to_msg_id = int(entry.get("reply_to_msg_id") or 0)
    chat_id = int(entry.get("chat_id") or 0)
    if reply_to_msg_id and (chat_id, reply_to_msg_id) in own_command_keys:
        return True
    if _is_own_settlement_summary(entry):
        return True
    if any(marker in text for marker in ("元神归窍总结", "野外历练")) and _mentions_scope_username(text, usernames):
        return True
    return False


def build_daily_rare_report(day=None, messages_dir=None):
    if day is None:
        day = datetime.now(TZ_LOCAL).strftime("%Y-%m-%d")
    else:
        day = str(day)
    entries, paths = _iter_daily_log_entries(day, messages_dir)
    identity_ids, usernames = _build_identity_scope()
    own_command_keys = _build_own_command_keys(entries, identity_ids)
    counts = Counter()
    source_counts = defaultdict(Counter)
    matched_entries = 0
    for entry in entries:
        if not _entry_matches_identity_scope(entry, identity_ids, usernames, own_command_keys):
            continue
        text = entry.get("text") or ""
        event_type = entry.get("event_type") or ""
        events = parse_rare_material_events_from_text(text, event_type=event_type)
        if not events:
            continue
        matched_entries += 1
        for event in events:
            counts[event.item] += event.count
            source_counts[event.item][event.source] += event.count
    return {
        "day": day,
        "counts": dict(counts),
        "source_counts": {item: dict(sources) for item, sources in source_counts.items()},
        "entry_count": len(entries),
        "matched_entries": matched_entries,
        "paths": paths,
    }


def _format_item_list(items, limit=12):
    if not items:
        return "无"
    sorted_items = sorted(items, key=lambda item: (-item[1], item[0]))
    shown = sorted_items[:limit]
    text = "、".join(f"{name} +{count}" for name, count in shown)
    remain = len(sorted_items) - len(shown)
    if remain > 0:
        text += f" 等 {remain} 类"
    return text


def format_daily_rare_report(report):
    counts = Counter(report.get("counts") or {})
    day = str(report.get("day") or "")
    focus_text = "｜".join(
        f"{FOCUS_DISPLAY_NAMES[item]} +{counts.get(item, 0)}"
        for item in FOCUS_ITEMS
    )
    fragment_items = [
        (display, counts.get(item, 0))
        for item, display in FRAGMENT_ITEMS.items()
        if counts.get(item, 0) > 0
    ]
    other_items = [
        (item, count)
        for item, count in counts.items()
        if item not in FOCUS_ITEMS and item not in FRAGMENT_ITEMS and count > 0
    ]
    lines = [
        f"📦 稀有物资日报 {day}",
        f"重点：{focus_text}",
    ]
    if fragment_items:
        lines.append(f"残纹：{_format_item_list(fragment_items, limit=6)}")
    lines.append(f"其他：{_format_item_list(other_items)}")
    lines.append("口径：只统计产出/换取/拼合/结算，不含储物袋快照、交易、消耗、归还。")
    return "\n".join(lines)


def _load_report_state():
    global _report_state_loaded, _report_state
    if _report_state_loaded:
        return _report_state
    _report_state_loaded = True
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    except Exception:
        data = {}
    _report_state = data if isinstance(data, dict) else {}
    return _report_state


def _save_report_state(data):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp_path = f"{STATE_FILE}.tmp.{os.getpid()}"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, STATE_FILE)


async def run_rare_daily_report_scheduler(now):
    global _last_sent_day_memory, _next_retry_at
    local_now = datetime.fromtimestamp(float(now), TZ_LOCAL)
    if local_now.hour < REPORT_HOUR:
        return False
    day = local_now.strftime("%Y-%m-%d")
    state = _load_report_state()
    if state.get("last_report_day") == day or _last_sent_day_memory == day:
        return False
    if float(now) < _next_retry_at:
        return False

    try:
        report = build_daily_rare_report(day)
        message = format_daily_rare_report(report)
        ok = await send_audit_log(message, scope="global", limit=900)
        if not ok:
            _next_retry_at = float(now) + 5 * 60
            return False
        state["last_report_day"] = day
        state["last_sent_at"] = int(time.time())
        state["matched_entries"] = int(report.get("matched_entries") or 0)
        _save_report_state(state)
        _last_sent_day_memory = day
        return True
    except Exception as exc:
        _next_retry_at = float(now) + 5 * 60
        await send_audit_log(f"📦 稀有物资日报生成失败：{exc}", scope="global", limit=260)
        return False


__all__ = [
    "RareMaterialEvent",
    "build_daily_rare_report",
    "format_daily_rare_report",
    "parse_rare_material_events_from_text",
    "run_rare_daily_report_scheduler",
]
