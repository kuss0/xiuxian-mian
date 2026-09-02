"""每日 /grow —— 额外功能，与修仙玩法无关。

不走 send_game_command：那条路会把 target_chat_id 校验成"必须是已配置的游戏群"
（runtime.py:3915），而且带一整套回包匹配/补发机器。`/grow` 没有需要匹配的游戏回包，
也不该受修仙侧的重试策略影响，所以这里用账号 client 直发，一次成败即结束。

只用实体账号发，不用频道 send_as 身份：DickGrowerBot 按真人用户统计，
且 19 个频道身份当前处于 SendAsPeerInvalidError 冻结态。
"""

import random
import time

from ..config import get_registered_client, is_account_offline
from ..persistence import save_state
from ..runtime import _run_account_rpc, console_log
from ..state import (
    get_accounts,
    get_daily_grow_last_sent,
    set_daily_grow_last_sent,
)


# 群用公开用户名解析，比数字 ID 稳（红包监听同样按用户名匹配）。
DAILY_GROW_CHAT = "smnet_group"
# 闲聊分组。forum 的 General 分组话题号就是 1；0 表示不指定话题、直发群里。
DAILY_GROW_TOPIC_ID = 1
DAILY_GROW_COMMAND = "/grow@DickGrowerBot"

# 总开关。目前没有 UI 能改它，所以放在代码里随部署走 —— 留一个界面上打不开的
# 状态开关，只会让功能看起来是坏的。要停就改这里重新部署。
DAILY_GROW_ENABLED = True

# jfdffdddd —— 按要求排除。
DAILY_GROW_EXCLUDED_ACCOUNT_IDS = frozenset({301299112})

# 空集 = 除排除项外的全部实体账号。收窄成具体账号可用于试发。
# 落点已于 2026-09-02 用 xianxia9527 单号试发确认（msg=107880，闲聊分组无误），
# 随后开放全部四个账号。
DAILY_GROW_ACCOUNT_ALLOWLIST = frozenset()

# 摊开在白天，别让几个号在同一分钟一起冒出来。
DAILY_GROW_WINDOW_START_HOUR = 10
DAILY_GROW_WINDOW_END_HOUR = 22
DAILY_GROW_MIN_GAP_SEC = 420.0

_last_send_ts = 0.0


def _today_key(now):
    return time.strftime("%Y-%m-%d", time.localtime(float(now)))


def within_send_window(now):
    """窗口既是下界也是上界。

    只判"是否已过发送时刻"会让 22:00 之后所有已到点的账号继续合格，
    于是深夜里每 7 分钟接着发一个 —— 2026-09-02 22:12 真出过这一幕。
    """
    hour = time.localtime(float(now)).tm_hour
    return DAILY_GROW_WINDOW_START_HOUR <= hour < DAILY_GROW_WINDOW_END_HOUR


def target_account_ids():
    """今天该发的实体账号，已排除 jfdffdddd 并按试发白名单收窄。"""
    account_ids = []
    for raw_id in (get_accounts() or {}):
        try:
            account_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if account_id in DAILY_GROW_EXCLUDED_ACCOUNT_IDS:
            continue
        if DAILY_GROW_ACCOUNT_ALLOWLIST and account_id not in DAILY_GROW_ACCOUNT_ALLOWLIST:
            continue
        account_ids.append(account_id)
    return sorted(account_ids)


def account_due_ts(account_id, now):
    """账号今天的发送时刻。按 (账号, 日期) 定死，同一天重算结果一致。"""
    day = _today_key(now)
    seed = random.Random(f"{account_id}:{day}")
    start = time.struct_time(time.localtime(float(now)))
    midnight = time.mktime((
        start.tm_year, start.tm_mon, start.tm_mday, 0, 0, 0,
        start.tm_wday, start.tm_yday, start.tm_isdst,
    ))
    span = (DAILY_GROW_WINDOW_END_HOUR - DAILY_GROW_WINDOW_START_HOUR) * 3600
    return midnight + DAILY_GROW_WINDOW_START_HOUR * 3600 + seed.random() * span


async def _send_grow(account_id):
    client_obj = get_registered_client(int(account_id))
    if client_obj is None:
        return False, "client 未注册"
    send_kwargs = {}
    if int(DAILY_GROW_TOPIC_ID or 0) > 0:
        # Telethon 用 reply_to 指定话题；直接发进话题就是这么发的。
        send_kwargs["reply_to"] = int(DAILY_GROW_TOPIC_ID)
    try:
        message = await _run_account_rpc(
            client_obj.send_message(DAILY_GROW_CHAT, DAILY_GROW_COMMAND, **send_kwargs),
            account_id=int(account_id),
            client_obj=client_obj,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"msg={int(getattr(message, 'id', 0) or 0)}"


async def run_daily_grow_scheduler(now=None):
    """一次 tick 最多发一个账号，失败不重试（明天自然再来）。"""
    global _last_send_ts

    if not DAILY_GROW_ENABLED:
        return False
    now = float(now or time.time())
    if not within_send_window(now):
        return False
    if now - _last_send_ts < DAILY_GROW_MIN_GAP_SEC:
        return False

    day = _today_key(now)
    sent_map = dict(get_daily_grow_last_sent() or {})
    for account_id in target_account_ids():
        if sent_map.get(str(account_id)) == day:
            continue
        if is_account_offline(account_id):
            continue
        if now < account_due_ts(account_id, now):
            continue

        ok, detail = await _send_grow(account_id)
        _last_send_ts = now
        # 成功与否都记账：/grow 是每日一次的娱乐命令，不值得为它重试刷发送量。
        sent_map[str(account_id)] = day
        set_daily_grow_last_sent(sent_map)
        save_state()
        console_log(
            f"🌱 每日 grow｜账号={account_id}｜{'成功' if ok else '失败'}｜{detail}",
            scope="global",
            limit=200,
        )
        return True
    return False


__all__ = [
    "DAILY_GROW_CHAT",
    "DAILY_GROW_TOPIC_ID",
    "DAILY_GROW_COMMAND",
    "account_due_ts",
    "run_daily_grow_scheduler",
    "target_account_ids",
    "within_send_window",
]
