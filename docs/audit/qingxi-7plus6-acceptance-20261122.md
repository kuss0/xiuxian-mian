# 青溪 7+6 真实验收：2026-11-22

继承 `qingxi-7plus6-acceptance-20261121.md` 与
`/home/xiu/xiuxian-7plus6-goal-plan-20261121.md` 的目标和最新更正：
两主号共 26 格，试炼固定第一重、单场零重试，最终仍须连续三个自然日无人值守。
本日目前确认 **1/26 通过**；自动整批与主线调度仍关闭，三日观察尚未开始。

## WA 麦田第一场：真实通关

前一维护会话停在第二波观察，但原生浏览器继续运行并正常结束。
2026-11-22 07:30 接手复核原始日志、服务端回执、宿主持久化与通知日志：

- 身份 `8659059191`，job `81d420cd16bc438c955d91206bca06b8`，
  run `X_tl234g8iZpdLbQ`，第一重，02:05:34 开局、02:22:51 通关。
- 恰一笔 `start`，同一 run 的第 0/1/2/3 波 checkpoint 各一笔；
  最终服务端 `run.status=cleared, cleared=3, tier=1, rewarded=true`，
  `activeRun=null`，当天 `used=1, remaining=2, limit=3`。
- 原生报告 `kind=victory, deaths=0, deathLog=[], retries=0`；
  游戏时间 323.43 秒、战斗墙钟 1037.15 秒，约 3.21:1。
- 真实奖励：灵石×560、法则碎片·火×2、天雷竹×1、回灵丹×1、
  青竹蜂云剑×1、修为＋400000。剑匣从 6/20 增至 7/20。
- 宿主 `server_closed=true/server_cleared=true/phase=done`，原 worker 已退出。
  journal 02:22:54 只有本场一次通知，`TG_NOTIFICATION_DELIVERY`
  `receipt_id=e36b1adfabcf4ac397d83b6ef3718fd3, outcome=confirmed`。

原始证据目录：
`/root/2026/qingxi-trial-m19-wa-canary-20261116/.runtime/8659059191/81d420cd16bc438c955d91206bca06b8/`。
接手断言及脱敏摘要：`/root/xiuxian-maintenance-20261122/record_longwind.py` 与
`wa-longwind-first-clear.json`。该通关不覆盖昨日战败，也不证明竹林或整批稳定。

## 本日主号 26 格

WA 07:31:51 正常只读奖励刷新已完成，九项服务端状态一致：七副本均未解锁、
未领奖且无 run；麦田 used=1/remaining=2，竹林 used=0/remaining=3，均无 activeRun。
吧唧 07:35:56 正常只读刷新完成：七副本亦未解锁、未领奖且无 run；
双试炼各 used=0/remaining=3/activeRun=null，没有本日通过证据。

| 槽位 | WA 8659059191 | 吧唧 301299112 |
| --- | --- | --- |
| 月殿血誓 | 待验：主线未解锁 | 待验：主线未解锁 |
| 血色禁地 | 待验：主线未解锁 | 待验：主线未解锁 |
| 南门守城 | 待验：主线未解锁 | 待验：主线未解锁 |
| 护民出关 | 待验：主线未解锁 | 待验：主线未解锁 |
| 血祭神坛 | 待验：主线未解锁 | 待验：主线未解锁 |
| 试傀场 | 待验：主线未解锁 | 待验：主线未解锁 |
| 剑冢 | 待验：主线未解锁 | 待验：主线未解锁 |
| 麦田 1 | 通过：X_tl234g8iZpdLbQ，四波通关、关闭、扣一次、通知已确认 | 待验 |
| 麦田 2 | 待验：剩余配额未尝试 | 待验 |
| 麦田 3 | 待验：剩余配额未尝试 | 待验 |
| 竹林 1 | 验证中：b8ffeb454d2d46dc85b640866d7476c0 | 待验 |
| 竹林 2 | 待验：未尝试 | 待验 |
| 竹林 3 | 待验：未尝试 | 待验 |

## 接续运行和占用

生产接手 HEAD `c5086584`，只有原有题库运行时修改。
`xiuxian.service`、watchdog、health observer active，发送队列和持久 pending 均空。
listener sidecar 维持既有停用设置，主 runtime 负责监听。

07:32:37 经正常认证入口仅安排 WA 竹林第一重有奖局，
job `b8ffeb454d2d46dc85b640866d7476c0`。真实子进程参数
`--runs 1 --tier 1 --retries 0 --max-run-seconds 1500`。
开局前新鲜服务端确认无在途局、竹林配额未用；本号下一元婴 11:07:52、
裂缝 10:34:14，普通灵宠 08:18:08，35 分钟预留在这些窗口之前。
只运行这一个战斗浏览器，尚未把加载或安排成功记为通关。
前台观察器 `/root/xiuxian-maintenance-20261122/observe_trial.py` 每 45 秒读取同一 job，
不会发游戏请求或修改状态。

防御预检另发现 wisemole 旧 job `37d35bea3d474521acae04bcd9e7cc74` 长期 held。
原生 runner 在人物加载阶段明确停止，试炼请求/回执数组均空；
现有 `no_start_verified` 谓词需要成功 status 回执，空日志不能自动解除。
该占用继续保留，后续须取得新鲜原局状态并做对应对账，不能把它记成试炼成功。
