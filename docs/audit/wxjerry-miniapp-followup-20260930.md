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

### 个人阵法只读面板

`.我的阵法` 接入独立 `cave_personal_formation` MiniApp 快照及 UI “个人阵法”动作。
保留已学阵法和当前防护的原文描述，不猜测消耗、有效期或冷却。
它不是星宫多人合阵：不读写 `formation_enabled`、助阵等待、启阵 CD 等原模块状态，
也不自动布阵/撤阵。只接受完整面板、匹配身份与原命令的完成回包；并发新快照和
保存失败不会被旧结果覆盖。

2026-09-30 真实只读验证：

- `/root/xiuxian-tianxing-panel-20260930.json`：频道选身份及 `.天机盘` 四次 HTTP 均 200，
  `playerId=-1003765328695`，动作确认完成；天机 40、无推命/改命。未写生产状态。
- `/root/xiuxian-formation-panel-20260930.json`：同频道个人阵法为空，接口可用。
- `/root/xiuxian-wa-formation-panel-20260930.json`：WA 已学大庚剑阵、四象御法阵、
  三才微尘阵、五行颠倒阵；防护无。查询没有布阵或消费道具。
- `/root/xiuxian-native-fishing-context-20260930.json`：频道 `fishing` 目录已为
  `integrated`，原生 `/fishing/context` 可用，有青竹钓竿，配额 0/5、剩余 5，
  无活动 session。该频道生产钓鱼开关本来关闭，不能把此次 0/5 归因为调度失败。
- 一次性工具 `tools/cave_readonly_probe.py` 只允许上述只读命令及 fishing/context；
  不导入生产 runtime，不写生产 DB，不发送群消息，不抛竿/买饵。
  独占创建输出文件以防中断重放；Telegram 使用只读会话的内存副本，报告不含认证数据。

## 接下来逐项迁移

| 项目 | 上游/本地差异 | 本轮处理 |
| --- | --- | --- |
| 洞府原生钓鱼 | `3c77db65` 新增 context/cast/hook/checkpoint/fight、operationId、持久回执；`3c220736` 补远航后恢复 | Lab 已完成单竿运行器、补饵/打窝、动作持久化、原子入账及公共入口手动验收调用。2200 项扩展测试通过；准备单号真实补给和单竿验收，普通调度尚未切换。三个已开身份原设置均为灵米饵、缺饵买20、米糠小窝；WA/吧唧远航不强行中断。详见 [原生钓鱼 Lab 记录](native-fishing-lab-20260930.md) |
| 天星前置 | 上游 `model/features/tianxing.py` 仍通过 Telegram CommandCandidate 发送 | 不可据此宣称上游已有 HTTP 推改。先交付查盘桥接；消费动作须逐条建立 HTTP 所有权、未知结果及路线互斥 |
| 分身管理 | 上游新增大规模分身状态与调度 | 不与本地 19 个频道身份模型混合迁移 |
| 灵兽等状态 | 本地已有部分命令入口或 Lab 证据，尚无完整状态桥接 | 保留待办，不将读到文本误报为自动化完成 |

## 验证和边界

- 初次天机盘集成关联回归：3414 passed、124 subtests。
- 补充异常结构、所有权、调度锁和失败保存后：55 项天机盘专项通过。
- 新版按钮专项：5 passed、15 subtests。
- 第一批扩大回归：3607 passed、153 subtests；UI 冒烟 18 项通过，JS 语法和 compileall 通过。
- 第一批 `80398561` 已提交、推送 `xiuxian-mian/main`，09:03 显式重启上线。
  16:29 核对仍为同一 PID `3450554`、`NRestarts=0`，健康观察持续为 ok。
- 第二批关联回归 3010 passed、161 subtests；补充 WA 实测面板后的最终专项
  260 passed，UI 冒烟仍为 18 项通过，JS/compileall/diff 检查通过。
- 17:13 前后两账号 Telegram 重连超时，17:18 后恢复收消息；17:26 健康恢复 ok，
  PID 未变、NRestarts=0、pending 队列为空。WA 17:05 已完成裂缝推命结算（天机 +1、贡献 +30）。
  没有为这次网络短断添加发送或恢复补丁。
- 第二批 `8a04ebaa` 已于 17:33:53 上线；17:36 健康为 ok，18:25 复核仍为
  同一 PID `3597994`、`NRestarts=0`。本轮新钓鱼实现仅在 Lab，不在这个生产版本内。
- World Boss 保持关闭；两号香火转神识保持关闭；频道发送冻结不变。
- 不改 CommandAttempt 控制权，不增加库存自动查询，不批量启用高风险动作。
