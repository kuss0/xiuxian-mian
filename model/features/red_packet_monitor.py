import asyncio
import re
import time
import unicodedata
from collections import OrderedDict

from ..forum_topic import event_topic_id
from ..runtime import console_log, send_audit_log, send_log_bot_notification


RED_PACKET_MONITOR_CHAT_USERNAME = "ja_netfilter_group"
RED_PACKET_MONITOR_TOPIC_ID = 458347
RED_PACKET_NOTIFICATION_CHAT_ID = -1004412426741
# 份数是可选的：`.发红包 20` 和 `.发红包 10 5` 都是真实用法，历史消息里各占一半。
# 不填时由 BOT 决定最终份数（≥20 LDC 默认 10 份，<20 LDC 上限 5 份），
# 所以这里只把它记成"未指定"，不猜具体值 —— 权威份数一律以发包播报卡片为准。
RE_RED_PACKET_COMMAND = re.compile(
    r"^\s*\.发红包\s+(?P<amount>\d+(?:\.\d+)?)(?:\s+(?P<count>\d+))?\s*$"
)
# 必须先认标记再取数字。旧写法用 `红包.*?` 起头，惰性匹配会从
# 「【LDC 红包已过期】…未抢：348.81 LDC / 1 份」一路扫到尾部并当成新包挂进待配对表
# （2026-09-03 02:20 实际发生）。之后若有人恰好发同额红包，就会弹出指向已过期消息的告警。
# 全量历史核对：252 条真发包卡片都带这个标记，误匹配的 8 条全是过期播报。
RE_RED_PACKET_CREATED_MARKER = re.compile(r"【\s*LDC\s*红包\s*】")
RE_RED_PACKET_CREATED = re.compile(
    r"红包.*?(?P<amount>\d+(?:\.\d+)?)\s*LDC\s*/\s*"
    r"(?P<count>\d+)\s*(?:份|个)"
)
_SEEN_CANDIDATES = OrderedDict()
_SEEN_CANDIDATE_LIMIT = 1000
_PENDING_COMMANDS = OrderedDict()
_PENDING_COMMAND_LIMIT = 100
_PENDING_COMMAND_TTL_SEC = 120
_PENDING_CREATED = OrderedDict()
_PENDING_CREATED_LIMIT = 100
_ALERTED_PACKETS = OrderedDict()
_ALERTED_PACKET_LIMIT = 500
_RED_PACKET_ALERT_THRESHOLD = 50.0
_RED_PACKET_ALERT_COUNT = 3
_RED_PACKET_ALERT_INTERVAL_SEC = 2.0
_ALERT_TASKS = set()


def _normalize_red_packet_text(text):
    normalized_text = unicodedata.normalize("NFKC", str(text or "")).replace("\u200b", "")
    return re.sub(r"\s+", " ", normalized_text).strip()


def parse_red_packet_command(text):
    normalized_text = _normalize_red_packet_text(text)
    match = RE_RED_PACKET_COMMAND.match(normalized_text)
    if not match:
        return None
    raw_count = match.group("count")
    return {
        "amount": float(match.group("amount")),
        "count": int(raw_count) if raw_count is not None else None,
    }


def parse_red_packet_created(text):
    normalized_text = _normalize_red_packet_text(text)
    if not RE_RED_PACKET_CREATED_MARKER.search(normalized_text):
        return None
    match = RE_RED_PACKET_CREATED.search(normalized_text)
    if not match:
        return None
    return {
        "amount": float(match.group("amount")),
        "count": int(match.group("count")),
    }


async def _event_chat_username(event):
    chat = getattr(event, "chat", None)
    username = str(getattr(chat, "username", "") or "").strip().lstrip("@").casefold()
    if username:
        return username
    try:
        chat = await event.get_chat()
    except Exception:
        return ""
    return str(getattr(chat, "username", "") or "").strip().lstrip("@").casefold()


def _claim_candidate(chat_id, message_id, event_type, text):
    key = (
        int(chat_id or 0),
        int(message_id or 0),
        str(event_type or "message"),
        str(text or ""),
    )
    if key in _SEEN_CANDIDATES:
        return False
    _SEEN_CANDIDATES[key] = None
    while len(_SEEN_CANDIDATES) > _SEEN_CANDIDATE_LIMIT:
        _SEEN_CANDIDATES.popitem(last=False)
    return True


def _prune_pending_commands(now):
    cutoff = float(now) - _PENDING_COMMAND_TTL_SEC
    for key, item in list(_PENDING_COMMANDS.items()):
        if float(item.get("created_at", 0) or 0) < cutoff:
            _PENDING_COMMANDS.pop(key, None)
    for key, item in list(_PENDING_CREATED.items()):
        if float(item.get("created_at", 0) or 0) < cutoff:
            _PENDING_CREATED.pop(key, None)


def _remember_pending_command(chat_id, message_id, parsed, now):
    if not parsed or parsed["amount"] < _RED_PACKET_ALERT_THRESHOLD:
        return
    key = (int(chat_id or 0), int(message_id or 0))
    raw_count = parsed.get("count")
    _PENDING_COMMANDS[key] = {
        "created_at": float(now),
        "amount": float(parsed["amount"]),
        "count": int(raw_count) if raw_count is not None else None,
        "topic_id": 0,
    }
    while len(_PENDING_COMMANDS) > _PENDING_COMMAND_LIMIT:
        _PENDING_COMMANDS.popitem(last=False)


def _claim_pending_created_packet(chat_id, message_id, parsed, now):
    # 只比金额。份数由 BOT 定夺（不填时按金额取默认值，填了也可能被上限截断），
    # 拿命令里的份数去比对播报卡片会在这些情况下静默失配。
    _prune_pending_commands(now)
    amount = float(parsed["amount"])
    for command_key, item in list(_PENDING_COMMANDS.items()):
        if command_key[0] != int(chat_id or 0):
            continue
        if item["amount"] != amount:
            continue
        if int(command_key[1]) >= int(message_id or 0):
            continue
        _PENDING_COMMANDS.pop(command_key, None)
        packet_key = (int(chat_id or 0), int(message_id or 0))
        if packet_key in _ALERTED_PACKETS:
            return None
        _ALERTED_PACKETS[packet_key] = None
        while len(_ALERTED_PACKETS) > _ALERTED_PACKET_LIMIT:
            _ALERTED_PACKETS.popitem(last=False)
        return {
            "packet_key": packet_key,
            "topic_id": int(item.get("topic_id", 0) or 0),
        }
    return None


def _remember_pending_created(chat_id, message_id, sender_id, topic_id, parsed, now):
    key = (int(chat_id or 0), int(message_id or 0))
    _PENDING_CREATED[key] = {
        "created_at": float(now),
        "amount": float(parsed["amount"]),
        "count": int(parsed["count"]),
        "sender_id": int(sender_id or 0),
        "topic_id": int(topic_id or 0),
    }
    while len(_PENDING_CREATED) > _PENDING_CREATED_LIMIT:
        _PENDING_CREATED.popitem(last=False)


def _claim_pending_command_for_created(chat_id, command_message_id, parsed, now):
    _prune_pending_commands(now)
    amount = float(parsed["amount"])
    for packet_key, item in list(_PENDING_CREATED.items()):
        if packet_key[0] != int(chat_id or 0):
            continue
        if packet_key[1] <= int(command_message_id or 0):
            continue
        if item["amount"] != amount:
            continue
        _PENDING_CREATED.pop(packet_key, None)
        if packet_key in _ALERTED_PACKETS:
            return None
        _ALERTED_PACKETS[packet_key] = None
        while len(_ALERTED_PACKETS) > _ALERTED_PACKET_LIMIT:
            _ALERTED_PACKETS.popitem(last=False)
        return {
            "message_id": packet_key[1],
            "sender_id": int(item.get("sender_id", 0) or 0),
            "topic_id": int(item.get("topic_id", 0) or 0),
            # 播报卡片才是权威：命令里的份数可能缺省或被 BOT 截断
            "parsed": {"amount": item["amount"], "count": item["count"]},
        }
    return None


def _event_topic_id(event):
    """话题号解析已收敛到 model/forum_topic.py，这里只提供本模块的已知话题号。"""
    return event_topic_id(event, known_topic_ids=(RED_PACKET_MONITOR_TOPIC_ID,))


def _red_packet_message_url(topic_id, message_id):
    base = f"https://t.me/{RED_PACKET_MONITOR_CHAT_USERNAME}"
    if int(topic_id or 0) > 0:
        return f"{base}/{int(topic_id)}/{int(message_id)}"
    return f"{base}/{int(message_id)}"


async def _send_red_packet_alerts(chat_id, topic_id, message_id, sender_id, parsed):
    message_url = _red_packet_message_url(topic_id, message_id)
    for index in range(1, _RED_PACKET_ALERT_COUNT + 1):
        alert_text = (
            "🧧 红包提醒｜来源群={chat_id}｜金额={amount:g} LDC｜数量={count} 份｜"
            "第 {index}/{total} 次提醒，请尽快抢｜{message_url}"
            .format(
                chat_id=int(chat_id or 0),
                amount=float(parsed["amount"]),
                count=int(parsed["count"]),
                index=index,
                total=_RED_PACKET_ALERT_COUNT,
                message_url=message_url,
            )
        )
        try:
            await send_audit_log(
                alert_text,
                scope="global",
                priority="high",
                limit=240,
            )
        except Exception as exc:
            console_log(
                f"🧧 红包提醒发送失败｜msg={int(message_id or 0)}｜{type(exc).__name__}: {exc}",
                scope="global",
                limit=240,
            )
        try:
            sent = await send_log_bot_notification(
                RED_PACKET_NOTIFICATION_CHAT_ID,
                alert_text,
                link_preview=False,
            )
            if not sent:
                console_log(
                    f"🧧 红包通知渠道发送失败｜chat={RED_PACKET_NOTIFICATION_CHAT_ID}｜msg={int(message_id or 0)}",
                    scope="global",
                    limit=240,
                )
        except Exception as exc:
            console_log(
                f"🧧 红包通知渠道异常｜chat={RED_PACKET_NOTIFICATION_CHAT_ID}｜"
                f"{type(exc).__name__}: {exc}",
                scope="global",
                limit=240,
            )
        if index < _RED_PACKET_ALERT_COUNT:
            await asyncio.sleep(_RED_PACKET_ALERT_INTERVAL_SEC)


def _schedule_red_packet_alert(chat_id, topic_id, message_id, sender_id, parsed):
    task = asyncio.create_task(
        _send_red_packet_alerts(chat_id, topic_id, message_id, sender_id, parsed)
    )
    _ALERT_TASKS.add(task)
    task.add_done_callback(_ALERT_TASKS.discard)


async def drain_red_packet_alert_tasks():
    """测试辅助：等待当前已排队的有限提醒完成。"""
    tasks = tuple(_ALERT_TASKS)
    if tasks:
        await asyncio.gather(*tasks)


async def observe_red_packet_candidate(event, *, event_type="message"):
    text = str(getattr(event, "raw_text", "") or "").strip()
    if "红包" not in text:
        return False
    if await _event_chat_username(event) != RED_PACKET_MONITOR_CHAT_USERNAME.casefold():
        return False
    topic_id = _event_topic_id(event)
    if topic_id != RED_PACKET_MONITOR_TOPIC_ID:
        return False
    chat_id = int(getattr(event, "chat_id", 0) or 0)
    message_id = int(getattr(event, "id", 0) or 0)
    safe_text = re.sub(r"\s+", " ", text)[:500]
    if not _claim_candidate(chat_id, message_id, event_type, safe_text):
        return True
    parsed = parse_red_packet_command(text)
    now = time.time()
    _prune_pending_commands(now)
    if parsed:
        _remember_pending_command(chat_id, message_id, parsed, now)
        pending = _PENDING_COMMANDS.get((chat_id, message_id))
        if pending is not None:
            pending["topic_id"] = topic_id
        matched_created = _claim_pending_command_for_created(chat_id, message_id, parsed, now)
        if matched_created:
            _PENDING_COMMANDS.pop((chat_id, message_id), None)
            _schedule_red_packet_alert(
                chat_id,
                int(matched_created.get("topic_id", 0) or 0),
                int(matched_created.get("message_id", 0) or 0),
                int(matched_created.get("sender_id", 0) or 0),
                matched_created.get("parsed") or parsed,
            )
    created = parse_red_packet_created(text)
    created_status = ""
    if created and created["amount"] < _RED_PACKET_ALERT_THRESHOLD:
        created_status = f"created=below_threshold:{created['amount']:g}/{created['count']}"
    elif created:
        matched = _claim_pending_created_packet(chat_id, message_id, created, now)
        if matched:
            alert_topic_id = topic_id or int(matched.get("topic_id", 0) or 0)
            _schedule_red_packet_alert(
                chat_id,
                alert_topic_id,
                message_id,
                int(getattr(event, "sender_id", 0) or 0),
                created,
            )
            created_status = "created=matched"
        else:
            _remember_pending_created(
                chat_id,
                message_id,
                int(getattr(event, "sender_id", 0) or 0),
                topic_id,
                created,
                now,
            )
            created_status = "created=waiting_command"
    parsed_text = (
        f"amount={parsed['amount']:g} count={parsed['count']}"
        if parsed
        else "unparsed"
    )
    console_log(
        f"🧧 红包候选观察｜type={event_type}｜chat={chat_id}｜msg={message_id}｜"
        f"sender={int(getattr(event, 'sender_id', 0) or 0)}｜"
        f"{parsed_text}{('｜' + created_status) if created_status else ''}｜text={safe_text}",
        scope="global",
        limit=720,
    )
    return True


__all__ = [
    "RED_PACKET_MONITOR_CHAT_USERNAME",
    "RED_PACKET_MONITOR_TOPIC_ID",
    "RED_PACKET_NOTIFICATION_CHAT_ID",
    "observe_red_packet_candidate",
    "parse_red_packet_command",
    "parse_red_packet_created",
    "RE_RED_PACKET_CREATED_MARKER",
    "drain_red_packet_alert_tasks",
]
