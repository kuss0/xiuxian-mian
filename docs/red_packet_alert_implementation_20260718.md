# 红包提醒实现

## 行为

- 监听 `@ja_netfilter_group`。
- 只把 `.发红包 金额 数量` 记为候选，不在指令出现时提醒。
- 等待天尊发布 `【LDC 红包】... 金额 LDC / 数量 份` 的成功回包。
- 金额低于 `50 LDC`、金额被拦截、没有成功回包时不提醒。
- 成功红包按消息 ID 去重，连续提醒 3 次，每次间隔 2 秒。
- 同一提醒同时发送日志群和通知渠道 `-1004412426741`；通知渠道强制使用 Bot API，不回退账号发送。
- 提醒内容带原红包消息的直达链接：
  `https://t.me/ja_netfilter_group/<话题ID>/<红包消息ID>`。
- 没有话题 ID 时退化为：
  `https://t.me/ja_netfilter_group/<红包消息ID>`。

## 独立实现

下面的实现不依赖 Telethon。宿主只需要把 Telegram 事件转换为具有这些属性的对象：
`raw_text`、`chat.username`、`chat_id`、`id`、`sender_id`、`message.reply_to`。

`send_alert(text)` 是宿主提供的异步日志群发送函数；宿主可以在其中追加管理员 mention。

```python
import asyncio
import re
import time
from collections import OrderedDict


class RedPacketMonitor:
    CHAT_USERNAME = "ja_netfilter_group"
    THRESHOLD = 50.0
    PENDING_TTL_SEC = 120
    ALERT_COUNT = 3
    ALERT_INTERVAL_SEC = 2.0

    COMMAND_RE = re.compile(
        r"^\s*\.发红包\s+(?P<amount>\d+(?:\.\d+)?)\s+(?P<count>\d+)\s*$"
    )
    CREATED_RE = re.compile(
        r"【LDC\s*红包】.*?(?P<amount>\d+(?:\.\d+)?)\s*LDC\s*/\s*"
        r"(?P<count>\d+)\s*份"
    )

    def __init__(self, send_alert, log=print):
        self.send_alert = send_alert
        self.log = log
        self.pending = OrderedDict()
        self.alerted = OrderedDict()
        self.tasks = set()

    @classmethod
    def parse_command(cls, text):
        match = cls.COMMAND_RE.match(str(text or ""))
        if not match:
            return None
        return {
            "amount": float(match.group("amount")),
            "count": int(match.group("count")),
        }

    @classmethod
    def parse_created(cls, text):
        match = cls.CREATED_RE.search(str(text or ""))
        if not match:
            return None
        return {
            "amount": float(match.group("amount")),
            "count": int(match.group("count")),
        }

    @staticmethod
    def topic_id(event):
        reply_to = getattr(getattr(event, "message", None), "reply_to", None)
        if reply_to is None:
            return 0
        return int(
            getattr(reply_to, "reply_to_top_id", 0)
            or getattr(reply_to, "reply_to_msg_id", 0)
            or 0
        )

    @classmethod
    def message_url(cls, topic_id, message_id):
        base = f"https://t.me/{cls.CHAT_USERNAME}"
        if int(topic_id or 0) > 0:
            return f"{base}/{int(topic_id)}/{int(message_id)}"
        return f"{base}/{int(message_id)}"

    def _prune(self, now):
        cutoff = now - self.PENDING_TTL_SEC
        for key, item in list(self.pending.items()):
            if item["created_at"] < cutoff:
                self.pending.pop(key, None)

    def _remember_command(self, event, parsed, now):
        if not parsed or parsed["amount"] < self.THRESHOLD:
            return
        key = (int(event.chat_id), int(event.id))
        self.pending[key] = {
            "created_at": now,
            "amount": parsed["amount"],
            "count": parsed["count"],
            "topic_id": self.topic_id(event),
        }
        while len(self.pending) > 100:
            self.pending.popitem(last=False)

    def _claim_created(self, event, parsed, now):
        self._prune(now)
        chat_id = int(event.chat_id)
        message_id = int(event.id)
        for command_key, item in list(self.pending.items()):
            if command_key[0] != chat_id or command_key[1] >= message_id:
                continue
            if item["amount"] != parsed["amount"] or item["count"] != parsed["count"]:
                continue
            self.pending.pop(command_key, None)
            packet_key = (chat_id, message_id)
            if packet_key in self.alerted:
                return None
            self.alerted[packet_key] = None
            while len(self.alerted) > 500:
                self.alerted.popitem(last=False)
            return item["topic_id"]
        return None

    def _schedule_alert(self, event, topic_id, parsed):
        async def run():
            url = self.message_url(topic_id or self.topic_id(event), event.id)
            for index in range(1, self.ALERT_COUNT + 1):
                text = (
                    f"红包提醒｜金额={parsed['amount']:g} LDC｜数量={parsed['count']} 份｜"
                    f"第 {index}/{self.ALERT_COUNT} 次提醒，请尽快抢｜{url}"
                )
                try:
                    await self.send_alert(text)
                except Exception as exc:
                    self.log(f"红包提醒发送失败：{type(exc).__name__}: {exc}")
                if index < self.ALERT_COUNT:
                    await asyncio.sleep(self.ALERT_INTERVAL_SEC)

        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def observe(self, event):
        chat = getattr(event, "chat", None)
        username = str(getattr(chat, "username", "") or "").lstrip("@").casefold()
        if username != self.CHAT_USERNAME:
            return False

        text = str(getattr(event, "raw_text", "") or "").strip()
        if "红包" not in text:
            return False

        now = time.time()
        self._prune(now)
        command = self.parse_command(text)
        self._remember_command(event, command, now)

        created = self.parse_created(text)
        if created and created["amount"] >= self.THRESHOLD:
            topic_id = self._claim_created(event, created, now)
            if topic_id is not None:
                self._schedule_alert(event, topic_id, created)
        return True

    async def drain_alerts(self):
        if self.tasks:
            await asyncio.gather(*tuple(self.tasks))
```

## 接入方式

在新消息和编辑消息入口都调用观察器，不要在观察器命中后 `return`，避免绕过宿主原有的消息账本和回复状态机：

```python
monitor = RedPacketMonitor(send_alert=send_audit_log)

@client.on(events.NewMessage())
async def on_message(event):
    await monitor.observe(event)
    await existing_message_handler(event)

@client.on(events.MessageEdited())
async def on_message_edited(event):
    await monitor.observe(event)
    await existing_edit_handler(event)
```

关键约束：成功回包是业务事实，指令只是候选；提醒必须有“候选指令 + 金额/数量一致的成功创建回包”这两个证据。不要在超时、拦截或普通聊天出现“红包”时直接发送提醒。
