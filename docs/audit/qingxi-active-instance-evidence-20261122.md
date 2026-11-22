# 青溪原局状态和占用保护

生产基线 7adfe49e；隔离候选分支
`fix/qingxi-active-instance-evidence-20261122`。

## 真实问题

WA 原任务 `quest-d79a740affa740efbf824292afc39302` 已确认交付 q30、
巨剑门 q31，q32 血色禁地 active。该任务通过官方入口收到 blood-land
start 成功回执，但助手报告 `api_unreachable` 并返回洞府。
原结果正确身份、原生停止、飞行关闭和请求 pending 为空，只能证明任务
worker 收尾；没有该副本原局的 end/关闭回执。
随后正常奖励状态查询返回 `qingxi_reward_daily_status_unverified`，
旧实现未保存副本 run，宿主仍 waiting_gameplay、gameplay_uncertain=false。

## 修复范围

七副本 status 的响应按原官方 envelope 解读。存在 run 时保存有限字段：
本人身份、副本、查询时间、服务端日期、run ID、机会/领奖标记、有限时间戳
及 operation ID；不保存 seed、token 或整个原始 run。
格式完整的 run 提供精确范围的复查依据；成功状态中存在的异常/不完整 run
也保持占用，但不因此允许自动复查或放行。

发现原局后持久化 pending_instance、held 和 gameplay_uncertain。
pending_instance 本身参与洞府活动占用和新任务的入口拦截，即使其他旧标记
后来被清除也不会重开。已停止的待核对局沿用既有范围：占用洞府场景，
不阻止明确忽略该场景占用的群文本活动。

只允许本人、同一副本、无活跃 worker、无原任务 pending/gather 的
reward_one_refresh。复查只发送该资源 status；不进入 3D、不 start/end、
不发奖励通知、不覆盖原任务证据或旧收获。即使服务端槽位已空或 run ID
不同，也保留原 pending_instance，等待原局关闭和结算证据。

vendor 仍为完整上游原版 2.0.114；没有新增战斗/地图实现、固定战报或开关。
自动 NPC 和整批仍关闭；其他账号和旧试炼记录不在本次核对范围。

## 隔离验证

定向回归 500 passed，覆盖保留原证据、跨身份/副本拒绝、原局状态异常、
旧占用标记被清除、活动范围，以及无新开局/无放行的同副本复查。
Ruff、Python 编译和 git diff --check 通过。
全量回归 **19451 passed、1561 subtests passed in 555.44s**，日志位于
`/root/xiuxian-maintenance-20261122/instance-review-full.log`。
tests/conftest.py 使用独立临时数据目录；未设置 live test DB 例外。

## 发布与真实复查

补丁 371a649c 已快进合入生产并推送 xiuxian-mian/main。
14:18:28 仅重启 xiuxian.service，MainPID 2919556、NRestarts=0；
14:18:49 恢复 24 个身份成功。发布前实际 UI 队列与持久 pending 为空，
没有青溪 worker；一致性备份 instance-review-pre-release.db quick_check=ok。

14:19 正常 rewards_refresh 确认 WA 血色禁地原局
`9ce59966fb81bab7649b116d`，服务端日期 2026-11-22，claimed=false，
hasKey=true，startedAt=1795322833.8428152。run.rewarded=true 是有奖局
资格，不代表已经领奖或通关。pending_instance 已持久化，宿主 held、
gameplay_uncertain=true。

14:20 正常 reward_one_refresh 仅查询 bloodland，仍返回同一 run ID。
原 pending_instance 完全保留；原 browser、receipts、observed、
reward_batch、reward_one_runs 和 notice_attempted 与备份相同。
两次复查共三笔成功 status：月殿一次、血色禁地两次；无 start/end、
无新浏览器、无结算或奖励通知。

UI HTTP 200、发送队列与持久 pending 均为 0，watchdog ok；
service、safety watchdog、health observer 均 active，启动后未见新运行时异常。
health observer 仍报告业务待核对和历史通知未确认，不将这些记录称为全绿。
NPC 与整批自动开关仍 false，题库运行时修改保留。

真实复查和请求审计位于 `/root/xiuxian-maintenance-20261122/` 的
instance-rewards_refresh.json、instance-reward_one_refresh.json 和
instance-review-request-audit.json。q32 仍未完成；须取得该原局关闭/结算
回执后才继续，不能用空槽查询、旧计时或本地“已停止”替代结束证据。
