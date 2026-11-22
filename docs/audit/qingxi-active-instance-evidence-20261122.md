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

尚未发布。发布后仅通过正常入口查询 WA 奖励状态以登记原局，再做同副本
只读复查；是否仍有原局以届时新鲜服务端响应为准，不以旧消息推断。
