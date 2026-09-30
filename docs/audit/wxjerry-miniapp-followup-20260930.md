# wxjerry 与 MiniApp 跟进（2026-09-30）

## 上游基线

- SSH fetch 后 `origin/main` 从 `fefb14b7` 更新到 `aa9dba29`。
- `/root/xiuxian-wxjerry-main` 已快进到该版本；没有把上游整体合并进生产。
- 上游最后一笔提交取消跟踪测试；本地不吸收这一做法，保留全部回归测试。

## 本次落地

### 新旧 Telegram 按钮结构

吸收 `cac8ab6` 的结构兼容：Telethon 1.45 的 URL/WebView 位于原始按钮的
`type` 对象。本地 `webapp_core._button_url` 现在先读该对象，再兼容旧版扁平按钮、
包装按钮以及嵌套 `web_app.url`。没有改变域名、bot、入口参数或发送者可信规则。
覆盖事件、Message、reply_markup、混合文本去重和不可信入口拒绝。

### 天机盘只读迁移

既有证据 `/root/xiuxian-channel-tianji-probe-20260919.json`：频道身份
`3765328695` 对应的 `playerId=-1003765328695`，公共入口选身份及
`.天机盘` 均成功，返回完整 `actionResult`，不是仅 HTTP 200。

新增实现：

- 命令适配器只增加精确 `.天机盘`，不开放任意命令或参数。
- 复用公共入口身份锁、双重所有者检查和限流；UI 公共入口增加“天机盘”。
- 回包必须确认 player、原命令、`ok=true`、`completed=true` 及完整的七组面板字段。
- 单独状态桥接，不调用会结算动作的 Telegram passive reducer。
- 推改证据明确标记 `event_type=miniapp`，保留身份和账号，不伪造群消息 ID。
- 效果截止时间按请求起点计算，不加 CD buffer 延长；旧证据不得覆盖新快照。
- 活动调度器、未结算命令、裂缝/斗法等待、近期放行、未知结果、损坏或更新的状态均拒绝覆盖。
- 不核销 pending、不改变 timer/配置/时间线、不增加命中收益；保存失败回滚内存。
- **这是手动只读校准入口，不是天星推命、定命、改命自动迁移完成。**

## 接下来逐项迁移

| 项目 | 上游/本地差异 | 本轮处理 |
| --- | --- | --- |
| 洞府原生钓鱼 | `3c77db65` 新增 context/cast/hook/checkpoint/fight、operationId、持久回执；`3c220736` 补远航后恢复 | 已核对协议；尚未接入本地运行器。需要先采集当前 context/session 合同，再接本地操作账本和结算计数，不能直接替换现有钓鱼 |
| 天星前置 | 上游 `model/features/tianxing.py` 仍通过 Telegram CommandCandidate 发送 | 不可据此宣称上游已有 HTTP 推改。先交付查盘桥接；消费动作须逐条建立 HTTP 所有权、未知结果及路线互斥 |
| 分身管理 | 上游新增大规模分身状态与调度 | 不与本地 19 个频道身份模型混合迁移 |
| 灵兽/阵法等状态 | 本地已有部分命令入口或 Lab 证据，尚无完整状态桥接 | 保留待办，不将读到文本误报为自动化完成 |

## 验证和边界

- 初次天机盘集成关联回归：3414 passed、124 subtests。
- 补充异常结构、所有权、调度锁和失败保存后：55 项天机盘专项通过。
- 新版按钮专项：5 passed、15 subtests。
- 扩大回归和部署后观察结果在下方续记。
- World Boss 保持关闭；两号香火转神识保持关闭；频道发送冻结不变。
- 不改 CommandAttempt 控制权，不增加库存自动查询，不批量启用高风险动作。
