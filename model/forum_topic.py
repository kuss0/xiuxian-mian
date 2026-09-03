"""Telegram forum 话题号解析 —— 单一实现。

Telethon 对 forum 群给出两种形态：

* 回复话题内的某条消息：``reply_to_top_id`` = 话题 ID，
  ``reply_to_msg_id`` = 被回复的那条消息。
* **直接发进话题**：``reply_to_top_id`` 为空，话题 ID 落在 ``reply_to_msg_id``。

只读 ``reply_to_top_id`` 会把后一种当成"不属于任何话题"。而后一种才是多数：
2026-09-02 抽查目标话题的 362 条消息，354 条是直接发进去的。

这个判断此前散落在五处且写法各异 —— 三处各自打了本地回落补丁绕开错误值
（wanxin、storage_bag、concubine），红包提醒因为没打补丁而彻底失效，
从上线起一次都没报过警。收敛到这里就是为了不再出现第六种写法。

保守原则：``reply_to_msg_id`` 在非 forum 群里就是普通的被回复消息 ID，
不能无条件当作话题号。只有 forum 标记为真、或它命中调用方给出的已知话题号时才认，
否则返回 0（与旧行为一致，绝不会比原来更糟）。
"""


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def resolve_topic_id(reply_header, *, known_topic_ids=()):
    """从 Telethon 的 reply header 解析话题号，解析不出返回 0。

    known_topic_ids: 调用方已知的话题号。当 forum 标记缺失时，用它来确认
    reply_to_msg_id 确实是话题而不是被回复的消息。
    """
    if reply_header is None:
        return 0

    top_id = _safe_int(getattr(reply_header, "reply_to_top_id", 0))
    if top_id > 0:
        return top_id

    forum_topic_id = _safe_int(getattr(reply_header, "forum_topic_id", 0))
    if forum_topic_id > 0:
        return forum_topic_id

    reply_to_msg_id = _safe_int(getattr(reply_header, "reply_to_msg_id", 0))
    if reply_to_msg_id <= 0:
        return 0
    if bool(getattr(reply_header, "forum_topic", False)):
        return reply_to_msg_id
    for known in known_topic_ids or ():
        if _safe_int(known) == reply_to_msg_id:
            return reply_to_msg_id
    return 0


def event_topic_id(event, *, known_topic_ids=()):
    """同上，但直接吃 Telethon 的 event（先取 event.message.reply_to）。"""
    message = getattr(event, "message", None)
    reply_header = getattr(message, "reply_to", None)
    if reply_header is None:
        reply_header = getattr(event, "reply_to", None)
    return resolve_topic_id(reply_header, known_topic_ids=known_topic_ids)


__all__ = ["event_topic_id", "resolve_topic_id"]
