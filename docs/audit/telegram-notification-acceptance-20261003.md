# Telegram 通知候选验收

验收后上线更新：用户后续已要求部署。代码 `814cc620` 于 2026-10-03 12:44 上线，仅开启回执采样，结构化摘要仍关闭；见 [第一阶段上线记录](telegram-notification-rollout-20261003.md)。以下“未部署”描述为验收当时的历史状态，生产效果尚未完成验收。

范围：`/root/xiuxian-tg-notifications-20261003`，基线 `a817c8de6370883cec9ea42f9d9235420945879e`。本次是通知首批候选验收，不是全量通知治理或生产部署授权。

## 结论

- **本批 Lab 代码验收通过**：验收发现的三处控制反馈缺陷已修复，最终固定版本全仓 `15727 passed, 1408 subtests passed`，9 个变动源码/工具文件 SHA-256 复核一致。
- 生产效果未验收：候选未提交、未部署，两项新开关仍默认关闭。缺少真实送达窗口，不给出实测降噪率，不批准直接开启全量新策略。
- 新增确认结果分组仅覆盖闭关和元婴。其他中高优先级消息、业务调度、红包和 watchdog 策略未改。

## 验收发现与修复

验收使用真实 `control.handle_log_group_command -> runtime -> AuditSummaryStore` 路径，仅替换 Telegram 出站和临时状态路径。新增用例首次运行是 **3 failed / 2 passed**，不是只补能通过的断言。

| 缺陷 | 修复 | 复验 |
| --- | --- | --- |
| 等待 30 分钟窗口、尚未发送却回复“发送失败” | 从实际摘要状态生成“等待摘要窗口”，不猜测传输结果 | 通过 |
| 未知投递已进入 held，却承诺“稍后会自动重试” | 明确显示“投递结果待核查，不自动重发” | 通过 |
| 待发为空但 held 非空，手动汇总仅称没有待汇总 | 保留待核查提示，不把 held 隐藏为空状态 | 通过 |

变更仅在 `runtime.get_audit_summary_delivery_note` 和手动汇总反馈接入；不增加强制发送、自动重放、游戏状态写入或新的通知通道。补充发送中查询、旧开关兼容、磁盘故障提示、高优先级不等待存储锁测试。

## 验收矩阵

| 项目 | 当前结果 | 依据/限制 |
| --- | --- | --- |
| 确认门槛与分组 | 通过 | 未解析、未知、跳过不获得确认标签；按稳定身份与完整内容哈希分组 |
| 24 身份合一条 | 通过，限模拟 | 不等于生产降噪率；不伪称成功轮数、全员完成或累计奖励 |
| 折叠、HTML、长度、意外提及 | 通过 | 20 行明细与 UTF-16 预算；普通用户名不主动 @ |
| 30 分钟限频、空摘要 | 通过 | 窗口未到不发；用户手动查询反馈不属于自动摘要预算 |
| 入队、重启、并发、取消 | 通过 | 同桶检查点；发送中新增记录留下一批；取消磁盘任务先收拢再解锁 |
| 未知投递 | 通过，保守策略 | 不跨窗口重发；明确失败/拒绝暂不能细分，也可能被保守保留为待核查 |
| 磁盘失效与容量 | 通过 | 不覆盖损坏快照；256 类明细+压缩计数；24h/7d；单条和总 payload 字节预算 |
| 手动控制反馈 | 修复后通过 | 新增端到端 8 项；不再承诺并不存在的自动重试 |
| 紧急消息与兼容 | 通过 | 不等待普通存储锁；关闭开关不打开摘要库；旧策略保持 |
| 生产投递基线 | 未通过 | 没有已采集的新格式回执；不能从控制台行数推导送达量 |
| 完整异常生命周期/批次成果 | 不在本批完成范围 | TG-02 剩余部分、TG-03/04/05 未销号 |
| Bot 超时后账号回退 | 未整改 | TG-06；单次调用内仍有潜在重复，不能宣称 exactly-once |
| 生产自然窗口 | 未开始 | 未部署，不拿 Lab 结果代替至少 24h 和完整自然日结观察 |

## 测试证据

- 修复前最终候选全仓：`15719 passed, 1408 subtests passed in 440.81s`。新增端到端验收发现的问题说明全仓绿色不是完整业务验收。
- 修复后定向：`198 passed, 47 subtests passed in 2.28s`，覆盖摘要、投递统计、日志群控制、启动、关停及展示。
- 修复后全仓：`15727 passed, 1408 subtests passed in 442.62s`。可重建 JUnit 证据：`data/analysis/tg-notification-acceptance-20261003.xml`。全仓运行期间未再修改源码或测试，不与定向测试数量相加。
- 全仓 Ruff、compileall、diff 检查通过。测试均使用 `XIUXIAN_ALLOW_LIVE_TEST_DB=0`，没有真实游戏请求或 Telegram 样式消息。

复验命令：

```bash
XIUXIAN_ALLOW_LIVE_TEST_DB=0 /opt/xiuxian-main/.venv/bin/python -m pytest -q --junitxml=data/analysis/tg-notification-acceptance-20261003.xml
/opt/xiuxian-main/.venv/bin/python -m ruff check .
/opt/xiuxian-main/.venv/bin/python -m compileall -q model tools tests
git diff --check
```

## 生产只读核对

- 2026-10-03 10:56 UTC+8 前后，生产 HEAD 仍为 `a817c8de`，主服务 PID `553961`、`active/running`、`NRestarts=0`，本次没有重启。
- watchdog 和 health observer 为 active；listener 为 inactive，本次未改变该既有配置，不将其描述为正常工作的冗余监听。
- 最新 24h 主服务 journal 读取 1428 行，投递报告 `available=false`。这说明未启用新回执采样，不代表发送了 0 条通知。证据：`data/analysis/notification-acceptance-baseline-20261003.json`。
- 生产 R2 备份相关改动保留，本次仅同步文档入口，不触碰生产代码、开关或数据库。

## 后续门槛

先独立审核并部署回执采样，保留结构化摘要关闭，采集真实基线。启用摘要前需确认保守 held 策略、存储异常的独立告警和 TG-06 的传输风险；灰度后观察至少 24h 及完整日结。其余模块逐批迁移，不直接把所有 normal/high 降级，也不提前删除兼容路径。

## 最终源码指纹

测试期间冻结以下源码，SHA-256 复核全部一致。文档和生成证据不包含在此指纹中。

```text
ab9750486cb8623a626e9adbbac9bbe47134a0cb8acf425e770796e60af04fa1  model/runtime.py
77807986f166bae9ebfa059bbb27bfadf0bf4ff942a854472f605440a87eb02d  model/control.py
8fa5c2741a9bce60f78f6646ead3b1b7f1244a9d378ab1732f7c545d836c6b97  model/audit_summary_store.py
660aac0d9bbe0a09e3365b87402ea264ac94c6fb9a31f663421a699e9be8d53e  model/audit_summary.py
513360011e187dad5cdfbad8c0ac53e8c8fa3e5aa510cc10ac1e39ef4f1ddca5  model/audit_delivery.py
bd9f4e11e093adfd0d7d6c4d50cc05c11cc7b7eb3033446e876f3d00e542992d  model/config.py
4b18b3a026ae8d6b6b4862d4b31ebf2a7a97284368306b835eb462887f910dde  model/app.py
94d901b1dff0eb95d58735068d3beb5f3154f04d0acef44fa24eba80f6c165d4  model/features/cave_treasure_runtime.py
37ee03be625b6d1ce21d971305dbaaf6d1854d615da57533001008eb4c78d859  tools/notification_report.py
```
