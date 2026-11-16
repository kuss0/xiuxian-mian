"""Opening limits are independent of dungeon rewards and participation."""
import re
from datetime import datetime, timedelta, timezone

TZ = timezone(timedelta(hours=8))
OPENERS = {
    ".出海猎妖": ("hunt", "【猎妖船】", "挂起猎妖旗"),
    ".阴冥之地": ("yinming", "【阴冥之地】", ".同入阴冥"),
    ".天澜草原": ("tianlan", "【天澜草原】", ".同行"),
    ".夜探越京": ("yuejing", "【越京夜行】", ".同往"),
    ".广寒界": ("guanghan", "【广寒界", ".入界"),
    ".魔金山脉": ("mojin", "【魔金山脉", ".入山"),
    ".开密议": ("qipai", "【七派密议】", ".入议"),
}
GROUP_COOLDOWN = 600
ROOT_DAILY_LIMIT = 2
GROUP_DAILY_LIMIT = 12
MARGIN = 15


def day(now):
    return datetime.fromtimestamp(now, TZ).strftime("%Y-%m-%d")


def next_day(now):
    return (datetime.fromtimestamp(now, TZ) + timedelta(days=1)).replace(
        hour=0, minute=5, second=0, microsecond=0).timestamp()


def rejection(text, at):
    text = text.strip()
    match = re.fullmatch(r"本群开局冷却中，还需\s*(\d+)\s*分钟。", text)
    if match:
        return {"scope": "cooldown", "until": at + max(1, int(match[1])) * 60 + MARGIN}
    match = re.fullmatch(r"(主魂|本群)今日发起次数已满（共 ([1-9]\d*) 局），北京时间零点后再来。", text)
    if match:
        return {"scope": "root" if match[1] == "主魂" else "group",
                "limit": int(match[2]), "until": next_day(at)}
    return None
