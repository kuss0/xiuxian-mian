"""Pure heart-trial text facts shared by gameplay and the independent watchdog."""

import re


PREFIX = "【坠魔心劫·"
FIRST, ACK1, SECOND, ACK2, THIRD, SETTLED = (
    PREFIX + suffix + "】" for suffix in (
        "第一轮", "第1轮已定", "第2轮", "第2轮已定", "第3轮", "结算",
    )
)
CHOICES, CULTIVATION, AFFINITY, DEMON = "三轮抉择", "修为结算", "情缘结算", "心魔值结算"
RE_HEADERS = re.compile(re.escape(PREFIX) + r"[^\r\n】]*】")
RE_OPTIONS = re.compile(r"^[^\r\n]*请(?:继续)?回复(?:本消息)?\s*"
                        r"\.稳\s*/\s*\.狠\s*/\s*\.骗[^\r\n]*$", re.MULTILINE)


def normalize(text):
    if not isinstance(text, str) or len(text) > 4096:
        return ""
    return re.sub(r"[*`]+", "", text).replace("\r\n", "\n").replace("\r", "\n").strip()


def parse_round(text):
    text = normalize(text)
    headers = RE_HEADERS.findall(text)
    if not headers or text.splitlines()[0] != headers[0] or text.count(PREFIX) != len(headers):
        return None
    number = {(FIRST,): 1, (ACK1, SECOND): 2, (ACK2, THIRD): 3}.get(tuple(headers))
    if (number is None or len(RE_OPTIONS.findall(text)) != 1
            or any(text.count(command) != 1 for command in (".稳", ".狠", ".骗"))
            or any(label in text for label in (CHOICES, CULTIVATION, AFFINITY, DEMON))):
        return None
    return number
