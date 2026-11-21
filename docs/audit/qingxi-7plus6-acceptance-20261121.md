# 青溪 7+6 真实验收

北京时间 2026-11-21。执行规格：`/home/xiu/xiuxian-7plus6-goal-plan-20261121.md`，采用其 02:50 更正：默认第一重，不执行旧高重或爬重方案。通过只认服务端通关/已领奖，目标仍是两主号 26 格及连续三个自然日。

## 接手与证据

- 08:42 核对 Claude PID 1500141：会话元数据为 `idle`，最近 assistant 回合已 `end_turn`，无子命令、无生产/战斗文件句柄。进程存活本身不等于另一个写入者，之前因此停工的判断过严。接手时生产 HEAD `764c6bfd`，仅题库未提交。未杀掉其他会话。
- 完整阅读执行规格、第 10 节列出的交接/审计、fanren 研究与本地 3D 结论；复核官方指令索引及 0.2.17 release。指令索引已改为动态加载 `/staticweb/guide/commands-v1.json`，不以空 HTML 的搜索结果判断玩法不存在。
- 本轮私有证据目录：`/root/xiuxian-tailnet-20261121.zSAVAf/`。SQLite 在线一致性备份 `chaogu_state.db`、API 配置前备份 `before-api-config.db`，均以 SQLite backup 从只读源生成。题库 SHA256：`5f82447d6ba00f5c555a0ec348fcb106aee3a92bc2a2fd09711d30cc3e634103`。
- 09:07–09:12 左右，依次对四号做九项奖励和斗法档案只读探针；均校验实际 playerId，无开局。原始脱敏结果在 `observations/<identity>-qingxi_rewards.json` 和 `<identity>-qingxi_trial_profile.json`。

## 主号 26 格事实表

以下“待验收”是阶段 0 初值，不是最终的“通过”或“角色限制”。当前 **0/26 通过**，尚未作角色限制结论。七副本两号均 `hasKey=false, claimed=false, failClaimed=false, run=null`；两场地均 `used=0, remaining=3, activeRun=null`。

| 槽位 | wa 8659059191 | 吧唧 301299112 |
| --- | --- | --- |
| 月殿血誓 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 血色禁地 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 南门守城 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 护民出关 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 血祭神坛 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 试傀场 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 剑冢 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 麦田 1 | 待验收：今日未开局 | 待验收：今日未开局 |
| 麦田 2 | 待验收：今日未开局 | 待验收：今日未开局 |
| 麦田 3 | 待验收：今日未开局 | 待验收：今日未开局 |
| 竹林 1 | 待验收：今日未开局 | 待验收：今日未开局 |
| 竹林 2 | 待验收：今日未开局 | 待验收：今日未开局 |
| 竹林 3 | 待验收：今日未开局 | 待验收：今日未开局 |

来源：上述本轮两主号 probe，每个接口单独保留 HTTP 时间和显式关闭字段。

## 陪跑号附表

| 槽位 | wisemole 8574677796 | 9527 8613500668 |
| --- | --- | --- |
| 月殿血誓 | 未解锁 | 未解锁 |
| 血色禁地 | 未解锁 | 未解锁 |
| 南门守城 | 未解锁 | 未解锁 |
| 护民出关 | 未解锁 | 未解锁 |
| 血祭神坛 | 未解锁 | 未解锁 |
| 试傀场 | 未解锁 | 未解锁 |
| 剑冢 | 未解锁 | 未解锁 |
| 麦田 1 | 未开局；旧任务 held | 已消耗；第二波战败，不通过 |
| 麦田 2 | 未开局 | 未尝试；auto_pause |
| 麦田 3 | 未开局 | 未尝试；auto_pause |
| 竹林 1 | 未开局；旧任务 held | 未尝试 |
| 竹林 2 | 未开局；旧任务 held | 未尝试 |
| 竹林 3 | 未开局；旧任务 held | 未尝试 |

七副本均未领奖且无 run；wisemole 双试炼各余 3，9527 麦田余 2、竹林余 3。两陪跑号斗法档案均无法宝槽位，主号优先。Lpp 已通过认证 API 移出 `qingxi_rewards` 名单，不为其补装备或验收。

## 角色与副本解锁

| 角色 | 境界 | 斗法法宝槽 | 飞剑/大招 | 青溪进度 |
| --- | --- | --- | --- | --- |
| wa | 炼虚初期 | 元合五极山、风雷翅、玄天斩灵剑、乾蓝冰焰、青竹蜂云剑（金雷竹·庚金相） | 72 / geng_formation | q00–q10 已领，q11 冷箭上的私印 active |
| 吧唧 | 化神后期 | 青竹蜂云剑（神雷版） | 72 / geng_formation | q00–q12 已领，q13 药匣与铜钥、q14 符火未冷 active |
| wisemole | 结丹后期 | 空 | 无飞剑 / empowered | 本轮未运行 NPC 主线探针 |
| 9527 | 结丹后期 | 空 | 无飞剑 / empowered | q00 初到青溪坊 available |

两主号补充只读资料：wa 根系术为奔雷咒，吧唧为烈焰焚天；两号均有南宫婉·月影在场、moonShadow=true、moonGuard=false。服务器试炼 best：wa 麦田 4/竹林 1，吧唧麦田 5/竹林 4。这些 best 不是本目标的真浏览器通关证据，自动请求仍固定第一重。原始资料分别为 `<identity>-full-doufa-profile.json` 和 `<identity>-raw-mainline-baseline.json`。

主号进度来自生产 `expedition_run_state.qingxi_quests[*].observed` 的新鲜只读回执；装备来自本轮服务端档案。已通最高重与完整功法差异仍须补充，不能据旧 HTTP 通关或 best 字段声称真浏览器通关。

包内 113 个 JS 已逐个做 SHA256 核验与青溪/战斗关键字检索，原始文件位于 `/root/fanren-research-20261120/analysis/local-3d/bundle/files/<sha256>`，URL 到 SHA 对应在 `analysis/manifest.json`。以下是客户端明确写出的奖励前置，须以后续真号 hasKey 变化验实。

| 副本 | 前置 | 客户端证据 |
| --- | --- | --- |
| 月殿血誓 | 完成 qx_q25 月殿禁符 | dwelling-field-combat.js:708 |
| 血色禁地 | 完成 qx_q31 七派之盟 | dwelling-field-combat.js:722、753 |
| 南门守城 | 完成 qx_q34 斩哨 | dwelling-field-combat.js:770、790 |
| 护民出关 | 完成 qx_q35 南门守城 | dwelling-field-combat.js:840、901 |
| 血祭神坛 | 完成 qx_q51 神坛之秘 | mulan-combat-content-v1.js:65 |
| 试傀场 | 完成 qx_q64 机关重组 | qingxi-baiqiao-v1.js:45 |
| 剑冢 | 完成 qx_q67 剑意初试（过试剑石） | qingxi-jianzhong-v1.js:177 |

q52、q65、q68 是对应战斗任务，本身不是进入对应有奖副本的前置。主号必须继续由原生任务浏览器推进。客户端奖励 level 跟境界缩放；七副本未见可选择“重数”，不套用双试炼 tier 规则。

## 旧账、通关证据与真实事实差异

- 吧唧仍 held：`field-release-mwmea4fz-2-2qahwy`，目标 `lantern:090`。wa 仍 held：`field-engage-mwopylxo-1-fhfj9j` 和 `field-cast:use:1795163441798`。两原 result.json 的 pending_operations 与 stored_pending 一致；wa 的 pending 数字为 4 但实际数组为两项，应以逐项数组核对而非数字计数。既有 operator_release 只接受这轮完整操作清单并重新读服务器。
- 已通过认证 `/api/expedition-config` 将两主号从旧长风名单移出，剩 Lpp、wisemole、9527；整批仍关闭。操作前后证据 `api-exclude-legacy-and-lpp.json`，当时真实内存发送队列为 0。
- wa 11-20 第四重原始证据已复核：job `6c17fd8bb0864d4e80cc82d01131283f`，run `4K9XwpgnKDMEIijB`。`runs.network.json` 最后回执 `run.status=ended, cleared=2, activeRun=null`；`runs.jsonl` 为 `kind=timeout, time=462.06, wallSeconds=1500.14`。只确认三波，第四波未通，cultivation=0；部分奖励灵石 339、九转解厄丹 1、法则碎片·火 2。不计通关。
- 9527 第一重 run `InbR-PX-3Tzumvdt` 是第二波战败；保持 auto_pause，不自动耗余次。Lpp 的既有战败只作为离线样本。
- 四冻结身份（3765328695、3800619925、3820064579、3823558636）目前 `tianxing_enabled=0, wild_training_enabled=1`，下一野外计时已在 11-22。这证明开关已经处理，真实历练回执及灵石收入尚需逐条补证，不重复切换或向群发言。

## 11-20/21 工作树收口

不删除或重置任何既有工作树。生产只接收需要的已验证改动；“保留”不代表候选已验收。

| 工作树后缀 | 处理与理由 |
| --- | --- |
| qingxi-late-body-20261121 | cbca7b63 已合入；保留只读历史 |
| xiuxian-changfeng-3d-20261120 | edd4e775 未整体合并；保留，功能已由后续生产实现覆盖，避免重复合流 |
| xiuxian-qingxi-2078-20261120 | 8f4be319 保留；生产已有上游 2.0.78，不重复套候选 |
| xiuxian-qingxi-abort-diagnostics-20261120 | d7cbf7f7 保留诊断历史；回包根因已由 0d28985d 修复 |
| xiuxian-qingxi-flight429-20261120 | 2e56e082 保留窄放行历史；生产已有，wa 已换成新 held |
| xiuxian-qipai-recovery-20261120 | 79db21da 已合入；保留 |
| fanren-native-input-20261120 | a60249db 已合入；保留研究证据 |
| fanren-process-cleanup-20261120 | 79f064ed 含 3 个未提交文件；原样保留，生产已有另一份收尾实现 |
| xiuxian-changfeng-browser-state-sync-20261120 | 4b6e4267 已合入；保留 |
| xiuxian-changfeng-notice-label-20261120 | 04bb654d 保留，现有 ce3c8102 已改通知，不重复合流 |
| xiuxian-changfeng-reward-notice-20261120 | bc75959d 保留，同上 |
| xiuxian-changfeng-tier1-20261120 | 73dff909 保留，生产已有一重硬锁 |

## 内网面板与发布验证

用户明确要求取消 TG 登录验证。本轮实现仅为 Tailscale 专用 Unix socket 提供面板权限，HTTPS 继续保留；普通 TCP 端口继续原鉴权。反代只允许实际 Tailnet 对端，覆盖客户端地址标头；专用 socket 检查 Host 与写请求 Origin。socket 仅应用与 Caddy 组可访问。Caddy 管理接口改为 caddy 用户私有 socket，避免非授权本机进程改反代去访问控制 socket。安装与回退方法见 `docs/TAILNET_UI.md`。

定向回归 38 passed；全量 `18833 passed, 1561 subtests passed in 555.43s`，使用隔离测试库。全量 Ruff、相关 Python 编译、`node --check`（整批和青溪策略）、`git diff --check` 通过。Caddy 候选配置 validation 通过。此处只表示实现验证通过，不表示游戏通关。

09:15:27 发布 87edc120，已推 xiuxian-mian/main，仅重启 xiuxian.service。主 PID 1799674，worker 1799677，NRestarts=0；24 个身份恢复成功，watchdog 与 health observer 保持运行。线上验证：Tailnet HTTPS GET /api/state=200，无 TG 登录；普通 TCP 无会话=401；非 Tailnet 来源=403；跨站写请求=403。Caddy reload 通过，管理端口 2019 已关闭，改为 caddy 用户私有管理 socket。证据 `auth-live-validation.json`。此后生产工作区仅余题库，阶段 0 出口满足。

09:16–09:17 两次 operator_release 各成功一次，归档请求与结果在 `wa-operator-review-request.json`、`baji-operator-review-request.json`、`quests-after-both-reviews.json`。原来的三条未决操作不重发，两号均恢复 waiting_gameplay/gameplay_uncertain=false；额外原文只读回包显示 flightLocked=false，目标怪存活、无对应尸体。

09:19:56 WA 新原生任务 worker `quest-4d64dbf7e35042419d5fe692977209d5` 启动，09:22:13 开始任务战斗，7 次击杀均被服务器确认，没有重现 HTTP 200 正文中止旧问题；qx_q11 箭簇进度仍为 2，未交付任务。09:26:54 一次新 field-engage 收到完整 JSON 的 HTTP 409 quest_too_far，本地错误地记成未知而 held。原任务 worker 已结束，未重开。主线调度通过 API 临时关闭以修这个新根因，7+6 总开关一直关闭。详见 `qingxi-engage-distance-20261121.md`。这次新 held 不属于原两次 operator_release 的授权范围，使用真实样本、窄条件与 revert-check 修复，不重复人工破例。

## 三日观察与资源账

未开始三日观察；不能把任何历史部分奖励、手动运行、离线回放折算进自然日。新增资源开支为 0，尚未购买或变更装备。

## 当前阶段与下一步

阶段 0 已满足出口：工作树盘点、26 格初值完成，发布后仅剩题库改动。阶段 1：九项奖励、主号境界/装备/术法/最高重/主线和七前置已核对。阶段 3 原两条旧账已放行并完成一次原生运行，但该次又出现一个不同的确定拒绝误记 held，修复和生产复核正在进行。阶段 2 的低画质/最小视距已验证，实际仅 1.55 FPS；打法、墙钟预算及当前玩法通关仍待实测。阶段 4/5/6 未开始。

1. 已完成 Tailnet 无 TG 登录与边界验证。
2. 已完成原两条 operator_release；新接敌距离拒绝修复须联合回归、发布后通过 refresh 再取服务器事实放行。
3. 原生浏览器按 wa 优先继续推主线；每次 bounded stop 按服务器状态续跑，未知不重试。
4. 补齐两场地/七副本打法、低画质 FPS、时间预算和本地资源缓存接入；符合阶段出口才进入单场验收。
5. 等待窗口按规格处理婉心失败生命周期、禁言停发、猎妖旧错误、通知统计和 HTTP 迁移盘点。
