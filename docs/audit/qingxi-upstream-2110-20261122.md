# 青溪上游 2.0.110 接入

2026-11-22。用户要求先检查上游更新，减少重复的本地处理。
候选基线 187c45bd，独立工作树 `/root/xiuxian-qingxi-upstream2110-20261122`。
最终全量验证已通过，待发布。

## 上游事实和采用范围

本机原 origin/main 与 vendor 是 abbb2c54、助手 2.0.78。
本轮 fetch origin/main 后，最新为 `8a6938d0eb0104f74dbf6edf6ff56cac92afb7ae`、
助手 2.0.110。区间有 19 个提交，全部差异位于 dwelling-autobot，
并未更新上游 model/features/concubine.py。

采用上游完整原版用户脚本，SHA256
`5940bf4c4bac8997e65caaa545f23383f7ab439cb62474f69b8693832d6a3991`；
vendor 字节与该 commit 的 git blob 完全相同。引入其原始 focus 测试。
Linux 宿主接入继续复用现有 Chromium/CDP、身份所有权、请求限速、待核对账本与收尾。
本轮没有切换到上游应用的持久化或整体替换生产项目。

直接复用的更新：

- 423e1f78、ee3e26ef：洞府返回、可达出口和主线推进。
- b37321f8：野外持续锁定、护送绕车与优先处理烧车者。
- 48534b8c、145fca01：相同试炼 checkpoint/operationId 的同步恢复与六场衔接。
- PR11 的月殿配装、瞬发技能、回灵间隔、破招灵力、HP 阶段门槛与符文移动。
- 副本/试炼前只在半阵时布置大庚剑阵，已有全阵时直接继续。

## 必要的 Linux 接入

最初诊断曾把已完成替换后的锚点也计为丢失；按原始 vendor 重新核对，
真正变化的是目标选择、带 blinkRetryAt 的步行变量和新增 movementRetries 的采集签名。
不复刻上游的新战斗控制器或月殿逻辑。

删除旧目标选择替换，采用上游 focusedEnemy/lockEnemy。
真实旧现场显示“原目标脱战 37 米外、另一个敌人已在近处交战”时，
上游 fightEnemy 初始强制锁定仍会选远处原目标。保留一处初始接管条件：
先选现有交战威胁，仅在原目标仍交战时显式覆盖。
回归现在执行实际生成的完整 fightEnemyCore，六种现场变体均通过；
只撤掉该条件时 live_stall 再次失败，其余五种通过。

保留已有步行防守耗时、真实碰撞重查、超时诊断和未领奖任务掉落核对。
适配新采集签名，并在上游三处递归恢复中传递 Q23 范围参数。
Q23 反击后仍须重新满足 420 秒剩余预算；不会丢失装瓶约束再发普通采集请求。

新版 `.布下剑阵` 通过 command-center 发出，没有 quests 风格的 action。
Linux 账本现在对这个明确命令先持久化意图、串行限速，再接受相同命令的
actionResult.ok=true/completed=true 回执。网络未知、未完成或回执命令不同保留 pending，
同页及重新载入均停止再次发送。只读模式、异身份、其他指令以及伪装成 status 的写入被拒绝。
没有新增 Telegram 发言入口，也没有启用 Electron 本地通知配置。

宿主从 vendor source.json 写入实际版本；支持 2.0.110 的普通收尾和相同格式的低血回包。
与特定旧故障对应的 2.0.78 历史复核分支保持原条件，不批量扩大一次性放行规则。
自动整批及 NPC 调度仍关闭；原 m19 单场入口和 hash 未切换。

## 验证和实机范围

隔离定向回归 576 passed；上游 Node focus 10/10，四种实际生成脚本的 Node 语法、
全量 Ruff、Python 编译及 diff 检查通过。目标防护撤修反例 1 failed/5 passed。
最终全量 **19397 passed、1561 subtests passed in 553.79s**。
之前为加入 command-center 边界中断的全量不计为通过。
日志与撤修证据均在 `/root/xiuxian-maintenance-20261122/`。

上游 checkpoint 恢复代码已采用，Linux 对未知写入继续保守停止。
本轮尚未验证新版的真实试炼同步恢复，不因此开启整批。
发布前 UI 队列、持久 pending 和模块 pending 均为 0，无浏览器任务；
一致性备份 `upstream110-pre-release.db` 已通过 SQLite quick_check。

本轮发布之后先用现有正常主线入口验证 WA q20，核对实际版本、身份、任务进度、
原生返回、服务端任务/飞行回执以及内存与磁盘 pending。
尚未以 2.0.110 完成任何真实主线、每日副本或试炼，不把隔离回归称为实机验收。
麦田旧 m19 的真实通关与今天竹林战败另见当天 7+6 验收文档。
