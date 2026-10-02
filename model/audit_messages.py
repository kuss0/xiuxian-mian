"""Telegram audit presentation only; no priority, delivery or game-state policy."""

from copy import copy
import html
import re

from telethon.extensions import html as telegram_html
from telethon.tl.types import MessageEntityBlockquote, MessageEntityPre


def text_units(text):
    return len(str(text).encode("utf-16-le")) // 2


def short_text(text, limit):
    text = str(text or "")
    if text_units(text) <= limit:
        return text
    return text.encode("utf-16-le")[:max(0, limit - 1) * 2].decode("utf-16-le", errors="ignore").rstrip() + "…"


def _html_slice(text, entities, start, end, *, quoted=False):
    offset, length = text_units(text[:start]), text_units(text[start:end])
    selected = []
    for entity in entities:
        if quoted and isinstance(entity, (MessageEntityBlockquote, MessageEntityPre)):
            continue
        left = max(offset, entity.offset)
        right = min(offset + length, entity.offset + entity.length)
        if right > left:
            item = copy(entity)
            item.offset, item.length = left - offset, right - left
            selected.append(item)
    return telegram_html.unparse(text[start:end], selected)


def bounded_html(body, limit):
    text, entities = telegram_html.parse(str(body or ""))
    if text_units(text) <= limit:
        return telegram_html.unparse(text, entities)
    clipped = short_text(text, limit)
    end = len(clipped) - 1
    return _html_slice(text, entities, 0, end) + "…"


_VISIBLE_MARKERS = (
    "需人工", "需要人工", "人工处理", "人工抉择", "待处理", "请手动",
    "禁止", "不要", "暂停", "安全锁", "冻结", "封禁", "停止", "🚨", "🆘", "⚠️",
    "失败", "异常", "不足", "拦截", "超时", "未完成", "未结算", "未通过",
)


def fold_audit_body(body, *, critical=False):
    text, entities = telegram_html.parse(body)
    if text_units(text) <= 240 and len(text.splitlines()) <= 3:
        return body
    if any(isinstance(entity, MessageEntityBlockquote) for entity in entities):
        return body
    lines = []
    pos = 0
    for line in text.splitlines(keepends=True):
        end = pos + len(line.rstrip("\r\n"))
        if line.strip():
            lines.append((pos, end, line.strip()))
        pos += len(line)
    if not lines:
        return body
    visible, folded = [], []
    for index, (start, end, line) in enumerate(lines):
        important = any(marker in line for marker in _VISIBLE_MARKERS)
        important = important or line.startswith(("获得：", "收获：", "下一步："))
        if index == 0 or important:
            if index == 0 and not critical and not important and text_units(line) > 180:
                preview = short_text(text[start:end], 180)
                visible.append(_html_slice(text, entities, start, start + len(preview) - 1) + "…")
                folded.append(_html_slice(text, entities, start, end, quoted=True))
            else:
                visible.append(_html_slice(text, entities, start, end))
        else:
            folded.append(_html_slice(text, entities, start, end, quoted=True))
    if not folded:
        return body
    return "\n".join(visible) + '\n<blockquote expandable>' + "\n".join(folded) + '</blockquote>'


def routine_copy(content):
    """Shorten only known successful templates, never interpret arbitrary errors."""
    text = str(content or "")
    voyage = re.fullmatch(
        r"👶\s*洞府天机阁元婴出窍：你心念一动，丹田中的元婴化作一道流光飞出，消失在天际。\s*"
        r"它将在外云游 \*\*(\d+)\*\* 小时，为你寻觅天地奇珍。下一次发言时若已归来，将自动结算收获。",
        text.strip(),
    )
    if voyage:
        return f"👶 元婴已出窍｜预计 {voyage.group(1)} 小时后归来"
    harvest = re.fullmatch(
        r"🌏\s*洞府小世界已收割香火：你大手一挥，将凡间供奉的 \*\*(\d+)\*\* 点香火尽数收入紫府。\s*"
        r"当前香火库存:\s*(\d+)", text.strip(),
    )
    if harvest:
        return f"🌏 收割香火 +{harvest.group(1)}｜库存 {harvest.group(2)}"
    text = re.sub(
        r"洞府闭关 start 完成：已同步｜阶段 running$",
        "闭关已开始", text,
    )
    text = re.sub(
        r"洞府闭关 settle 完成：已同步｜阶段 post_summary_wait$",
        "闭关结算已同步", text,
    )
    return text


def folded_summary_details(lines, *, limit=3200):
    body = short_text("\n".join(lines), limit)
    return '<blockquote expandable>' + html.escape(body) + '</blockquote>'
