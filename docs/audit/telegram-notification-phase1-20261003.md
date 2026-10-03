# Telegram 通知治理首批 Lab

最新进展：已按后续用户要求上线代码 `814cc620`，仅开启回执采样；见 [第一阶段上线记录](telegram-notification-rollout-20261003.md)。下文保留 Lab 历史状态，不能再用“未上线”描述当前代码；结构化摘要仍未启用。

正式验收已完成：本批 Lab 代码通过，生产效果仍待验证。以 [候选验收报告](/root/xiuxian-tg-notifications-20261003/docs/audit/telegram-notification-acceptance-20261003.md) 的最终测试、源码指纹和边界为准。

2026-10-03 收尾更新：后续已在独立 Lab 补齐同桶持久化、容量与取消保护，最终证据及未完成项见 [Lab 收尾记录](/root/xiuxian-tg-notifications-20261003/docs/audit/telegram-notification-phase1-20261003.md)。下文保留首批阶段记录，不表示当前仍未实现持久化；生产行为未变，候选未提交、未部署。

状态：候选实现，未部署；两个新开关默认均为关闭。未宣称 TG-01 真实基线验收或 TG-02 完整销号。

Lab：`/root/xiuxian-tg-notifications-20261003`，分支 `cleanup/telegram-notifications-20261003`，基线 `a817c8de`。

上位方案：[通知治理计划](telegram-notification-plan-20261003.md)。

## 已完成

1. `tools/notification_report.py`：静态扫描通知调用点，不导入生产 runtime、不打开数据库、不登录 Telegram。输出文件/行号、优先级、结构化覆盖和独立 Bot API 候选入口。生产源码盘点为 496 个直接调用、388 个隐式 auto、30 个显式 normal，与人工复核相符。
2. `model/audit_delivery.py`：可选投递回执元数据，包含时间、渠道、Bot/账号、confirmed/unconfirmed/unknown、耗时、可见 UTF-16 长度、显式提及链接数和正文摘要哈希；不保存正文、token、cookie、initData 或原始 URL。
3. `model/runtime.py`：在现有日志群及第二通知渠道传输完成处记录元数据；不改变发送、超时、回退、重试或取消语义。统计异常不能触发额外发送。
4. `model/audit_summary.py`：首批只接闭关与元婴已确认的解析结果。继续使用原汇总桶，不再建立第二个消息队列；使用模块、稳定身份 ID 和完整正文哈希区分记录，避免改名拆组和裁剪后误合并。
5. 已确认元婴结果不再因调用方传 normal 而必须单发，候选模式下与闭关一起进入不短于 30 分钟的摘要。未知、失败、未解析、跳过动作不打确认标签；显式 high、人工处理标记、带按钮和未迁移消息保留原路由。
6. 摘要固定按闭关、元婴、其他记录组织；每身份显示最新观察，标明身份数和记录数，不声称成功轮数、全员完成，不累计奖励。多模块明细交错取样，最多 20 行、折叠内容 2800 UTF-16 单元。
7. 发送失败回存时保留完整分组键，旧失败快照不得覆盖等待发送期间新入队的标签和时间；格式化异常也回存原桶。

## 验证口径

- 首轮相关测试：266 passed，69 subtests passed。
- 扩展回归：536 passed，378 subtests passed，覆盖消息发送、红包、封禁提醒、稀有日报、Boss 及闭关/元婴。不同测试批次有重叠，不累加为唯一测试数。
- 全仓 Ruff、compileall 和 diff 检查通过。全量隔离测试 `15699 passed, 1408 subtests passed in 453.33s`；临时数据库，未授权真实游戏请求。
- 模拟 24 个身份元婴确认消息，由 24 条 normal 实时发送变为一条摘要；不是实测生产降噪率，不能据此关闭剩余迁移任务。
- 已测改名、完整正文差异、失败回存并发、显示长度/转义/意外提及、解析确认门槛、Bot 超时后账号回退分开计数、取消传播、统计失败不影响发送。
- 生产源码盘点生成文件位于 Lab 的 `data/analysis/notification-baseline-20261003.json`，属于可重建产物。没有投递回执时报告明确 `available=false`，不把普通 journal 行视作送达，不输出虚构的零延迟分位数。
- 当前 24 小时 journal 实读 1585 行，没有新格式回执，报告为 `available=false`；证据 `data/analysis/notification-journal-baseline-20261003.json`。该结果说明采样尚未启用，不说明真实通知为零。

## 开关与报表

```text
LOG_GROUP_DELIVERY_METRICS=0
LOG_GROUP_STRUCTURED_SUMMARY=0
```

回执采样和结构化通知开关独立。后续可先仅部署/开启回执采样，完整新策略不得随之启用。

```bash
.venv/bin/python tools/notification_report.py --project-root /opt/xiuxian-main
journalctl -u xiuxian.service --since '24 hours ago' -o json --no-pager | .venv/bin/python tools/notification_report.py --journal -
```

统计单位是传输尝试，不是业务事件。只有明确正常返回才计 confirmed；未知请求可能已经到达 Telegram，报表不将 unknown 算作确定失败。相同 payload 哈希仅用于统计完全相同的投递，不代表同一业务事件。独立 watchdog/日报尚未接入此回执，报告明确标出覆盖范围；没有采集窗口心跳，暂不能仅凭稀疏回执推断完整全天覆盖率。

## 明确未完成

- TG-01：真实投递窗口、独立工具送达量、完整分级表尚需补齐，代码盘点不等于现场基线。
- TG-02：当前仍使用既有内存桶；重启恢复、有界持久化、容量策略、跨窗口事件去重、异常升级/恢复生命周期未完成。汇总任务取消时的保存与未知投递恢复也须在持久化批次验证。新分组不能据此直接在生产开启。
- 当前哈希只是完整内容分组，不是操作 ID 幂等；同一操作不同文案仍保留为多条观察，摘要展示最新观察，不核销业务。
- 小世界、钓鱼、观星台及普通故障尚未迁移；没有批量降低 high/medium。
- TG-03/04/05：整批成果、时效例外复核、独立通知入口、灰度后旧路径删除和后台设置尚未完成。
- TG-06：Bot 超时后账号回退风险仅增加分开统计与隔离测试，本批没有修改回退策略。
- 未重新发送任何游戏命令或样式消息，未修改生产开关、状态库、服务或无关 R2 改动。

## 下一批边界

先补回执采样验收和独立入口清单，再实现同一通知桶的持久恢复与容量边界；通过取消、重启、失败回存和未知投递测试后才考虑启用首批分组。后续逐模块用实际业务 ID 接入，不能把本批正文哈希包装成完整事件账本。
