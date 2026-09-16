"""Strict heart-trial text facts; parsing alone does not establish ownership."""

import re

from . import concubine as c


PREFIX = "\u3010\u5760\u9b54\u5fc3\u52ab\u00b7"
FIRST, ACK1, SECOND, ACK2, THIRD, SETTLED = (
    PREFIX + suffix + "\u3011" for suffix in (
        "\u7b2c\u4e00\u8f6e", "\u7b2c1\u8f6e\u5df2\u5b9a", "\u7b2c2\u8f6e",
        "\u7b2c2\u8f6e\u5df2\u5b9a", "\u7b2c3\u8f6e", "\u7ed3\u7b97",
    )
)
CHOICES, CULTIVATION, AFFINITY, DEMON = (
    "\u4e09\u8f6e\u6289\u62e9", "\u4fee\u4e3a\u7ed3\u7b97", "\u60c5\u7f18\u7ed3\u7b97", "\u5fc3\u9b54\u503c\u7ed3\u7b97",
)
CD = "\u5fc3\u52ab\u4f59\u6ce2\u672a\u6563"
PROGRESS = "\u4f60\u5df2\u6709\u4e00\u573a\u5fc3\u52ab\u6289\u62e9\u6b63\u5728\u8fdb\u884c"
ANCHOR = "\u5fc3\u52ab\u951a\u70b9\u5df2\u6563"
PANEL = "\u8bf7\u56de\u590d\u4e00\u6761\u5305\u542b\u4f8d\u59be/\u9053\u4fa3\u5185\u5bb9\u7684\u6d88\u606f"
COUNT = r"(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)"
SIGNED = r"[+-]?" + COUNT
RE_HEADERS = re.compile(re.escape(PREFIX) + r"[^\r\n\u3011]*\u3011")
RE_OPTIONS = re.compile(r"^[^\r\n]*\u8bf7(?:\u7ee7\u7eed)?\u56de\u590d(?:\u672c\u6d88\u606f)?\s*"
                        r"\.\u7a33\s*/\s*\.\u72e0\s*/\s*\.\u9a97[^\r\n]*$", re.MULTILINE)
RE_CHOICES = re.compile(r"[\u7a33\u72e0\u9a97]\s*/\s*[\u7a33\u72e0\u9a97]\s*/\s*[\u7a33\u72e0\u9a97]")
RE_DEMON = re.compile(r"(?P<delta>" + SIGNED + r")\s*[\uff08(]\u5f53\u524d\s*(?P<current>" + COUNT + r")[\uff09)]")
WAIT = r"[\uff0c,]\s*\u8bf7\u5728\s*(?P<wait>[^\r\n]+?)\s*\u540e\u518d\u8bd5"
RE_CD = re.compile(re.escape(CD) + WAIT + r"[\u3002.!\uff01]?")
RE_PROGRESS = re.compile(re.escape(PROGRESS) + "(?:" + WAIT + r")?[\u3002.!\uff01]?")
RE_ANCHOR = re.compile(re.escape(ANCHOR) + r"[\uff0c,]\s*\u9700\u91cd\u65b0\u5f15\u52a8\u5929\u52ab[\u3002.!\uff01]?")
RE_PANEL = re.compile(re.escape(PANEL) + r"[\uff0c,]\s*\u518d\u4f7f\u7528 \.\u5171\u5386\u5fc3\u52ab[\u3002.!\uff01]?")


def _number(text, *, signed=True):
    if not isinstance(text, str) or not re.fullmatch(SIGNED if signed else COUNT, text):
        return None
    value = int(text.replace(",", ""))
    return value if -(2 ** 63) < value < 2 ** 63 else None


def _settlement(text, at):
    fields = {}
    for label in (CHOICES, CULTIVATION, AFFINITY, DEMON):
        matches = list(re.finditer(r"^" + re.escape(label) + r"[\uff1a:]\s*([^\r\n]+)$", text, re.MULTILINE))
        if len(matches) != 1 or text.count(label) != 1:
            return None
        fields[label] = matches[0][1].strip()
    choices, demon = RE_CHOICES.fullmatch(fields[CHOICES]), RE_DEMON.fullmatch(fields[DEMON])
    cultivation, affinity = _number(fields[CULTIVATION]), _number(fields[AFFINITY])
    if choices is None or demon is None or cultivation is None or affinity is None:
        return None
    demon_delta, demon_current = _number(demon["delta"]), _number(demon["current"], signed=False)
    due_at = c._query_time(at + c.CONCUBINE_HEART_CD_SEC + c.CD_BUFFER_SEC)
    if demon_delta is None or demon_current is None or due_at is None:
        return None
    return {"outcome": "settlement", "choices": [part.strip() for part in fields[CHOICES].split("/")],
            "cultivation_delta": cultivation, "affinity_delta": affinity, "demon_delta": demon_delta,
            "demon_current": demon_current, "due_at": due_at, "text": text}


def _normalize(text):
    if not isinstance(text, str) or len(text) > 4096:
        return ""
    return re.sub(r"[*`]+", "", text).replace("\r\n", "\n").replace("\r", "\n").strip()


def is_anchor_lost(text):
    return RE_ANCHOR.fullmatch(_normalize(text)) is not None


def parse(text, observed_at):
    at, text = c._query_time(observed_at), _normalize(text)
    if at is None or not text or c._is_phaseful_summary_text(text) or c._is_heavenly_ban_text(text):
        return None
    headers = RE_HEADERS.findall(text)
    voyage = c._parse_voyage_rejection(text, at)
    flags = {"cooldown": CD in text, "in_progress": PROGRESS in text, "voyage_lock": bool(voyage),
             "anchor_lost": ANCHOR in text, "missing_panel": PANEL in text,
             "resource_shortage": c._is_heart_resource_shortage_text(text)}
    if sum(flags.values()) + bool(headers) != 1:
        return None
    if headers:
        if text.splitlines()[0] != headers[0] or text.count(PREFIX) != len(headers):
            return None
        if headers == [SETTLED]:
            return _settlement(text, at)
        round_no = {(FIRST,): 1, (ACK1, SECOND): 2, (ACK2, THIRD): 3}.get(tuple(headers))
        if (round_no is None or len(RE_OPTIONS.findall(text)) != 1
                or any(text.count(command) != 1 for command in (".\u7a33", ".\u72e0", ".\u9a97"))
                or any(label in text for label in (CHOICES, CULTIVATION, AFFINITY, DEMON))):
            return None
        return {"outcome": "round", "round": round_no, "text": text}
    if any(label in text for label in (CHOICES, CULTIVATION, AFFINITY, DEMON)) or PREFIX in text:
        return None
    outcome = next(key for key, present in flags.items() if present)
    if outcome in {"cooldown", "in_progress"}:
        matched = (RE_CD if outcome == "cooldown" else RE_PROGRESS).fullmatch(text)
        if matched is None:
            return None
        wait = matched["wait"]
        due_at = c._parse_wait_due_at(wait, at) if wait else 0
        if wait and (due_at is None or due_at <= at):
            return None
        return {"outcome": outcome, "due_at": due_at, "text": text}
    if outcome == "voyage_lock":
        return {"outcome": outcome, "voyage": voyage, "text": text}
    if outcome in {"anchor_lost", "missing_panel"}:
        if not (RE_ANCHOR if outcome == "anchor_lost" else RE_PANEL).fullmatch(text):
            return None
    elif not text.startswith("\u4fee\u4e3a\u4e0d\u8db3") or "\n" in text:
        return None
    return {"outcome": outcome, "text": text}
