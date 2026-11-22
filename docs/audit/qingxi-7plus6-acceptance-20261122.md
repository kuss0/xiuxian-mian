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
| 竹林 1 | 失败：V7AAXv8A5YCT_Ywn，首领波战败、原局关闭 | 待验 |
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

## 07:46 WA 竹林首领波战败

本局 07:36:21 开始，07:46:42 真实战败。前三波服务端确认（cleared=2），
末端 `run.status=defeated, activeRun=null`，当天 used=1/remaining=2。
只有一笔 start，deaths=1、retries=0；战斗游戏时间 213.94 秒、墙钟 633.78 秒。
原 worker 正常收尾退出，本号竹林今天停止，不开第二局。

部分奖励灵石×392、天雷竹×3、法则碎片·木×1、法则碎片·雷×1，修为为 0。
通知 07:46:47 已确认，receipt `57a286f64311484b81ed33da88fdbd67`。
这笔部分奖励不计通过，主号进度仍为 1/26。
摘要 `/root/xiuxian-maintenance-20261122/wa-bamboo-defeat.json`，
原目录为同一 runtime 下的 `b8ffeb454d2d46dc85b640866d7476c0/`。

07:46 时主记录的旧文字仍显示上一场麦田通关，但本局 browser_trial 和
defeats_by_arena 已准确记录竹林战败；验收以本局原生及服务端回执为准。
08:16 上游升级重启时，现有启动对账已依据新 browser_trial 重建主记录：
run_id 为 V7AAXv8A5YCT_Ywn，文字为已结束及本局部分奖励，server_cleared=false。

wisemole 07:37:13 只读探针确认两个场地均 activeRun=null、used=0/remaining=3，
七副本未解锁。证据 `wisemole-rewards-read.json`，未开局、未解除其旧 held。
竹叶清风（3888882303）的只读远航回包实际为“侍妾【南宫婉·月影】已自【月殿寻痕】
航线归来，正在洞府外等你接引。”，本地解析器拒绝该变体；未接引或结算。
用户要求先检查上游，独立远航修复工作树当前没有候选改动；先完成上游更新评估。

## 上游 2.0.114 与主线后续

2.0.110 已替换为完整原版 2.0.114（origin/main 860d9a1f、PR13）。
11:17 再次 fetch 以及作者 xifeng 分支复核没有更新提交；官方任务地图、
施法传输、野外战斗和月殿四份在线 JS 与现有 0.2.17 资源包逐字节一致。
新版仍由 Chromium 承载官方运行时，经原生 HTTP/WS 执行动作。

09:44 一轮实机已交付 q27、q28、q29；q30 击退枪手 3/3，但账册未取得。
原结果 quest-d645fb7ca31e423aa47b33bfb382f8c3 的新鲜收尾显示 q31 locked，
无 pending。巨剑门选项 juji an 的实际配置键为 `jujian`，已由用户指定；
尚未进行不可更改的 q31 挂单。

11:34 经正常入口继续 WA q30，job quest-65c74f88121b4950985ddc9378dd80ac。
原生已确认正确身份、满血入坊。一次 lantern:090 击杀结算 ok=true，
damage=250242、hits=409，但 q30 账册进度仍为 0。任务目录规定其掉率
0.85，不能仅凭本地击杀日志断言拾取成功。

第二次黑衣剑宗交战后，原生低血保护停止并返回洞府；最终气血 1056.02/15086，
无死亡或重试记录。原始受伤环保留四笔 HP 损失及近处招式候选，分别包括
bossDash、bossQi 和后续 stagger；候选不等于确认伤源或防守指令回执。
官方 enterWorld() 明确清零临时血瓶，运行时 hpPotions=0 不证明背包缺药。

最终正确身份、新鲜任务回执、server_checked=true、成功 field-release 已确认，
浏览器内存及磁盘 pending 均空，宿主 waiting_gameplay/gameplay_uncertain=false。
q30 账册仍 0/1，q31 locked。先分析本次证据，不重开同一战斗。
证据为该 job 的 result.json，以及
`/root/xiuxian-maintenance-20261122/wa-q30-upstream114-observed.jsonl`。
这次是主线低血收尾，不计入 26 格通过；本日仍为 1/26，整批/NPC 自动开关仍 false。

远航剩余回包问题由诊断 ba160348 和固定官方提示行 cebeaeaf 解决：
竹叶清风的正常只读入口已正确同步 returned，尚未由该读请求结算。
见 concubine-voyage-instruction-20261122.md。后续生产 HEAD 已推送；
题库运行时修改保留。
