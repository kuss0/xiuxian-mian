# 青溪 7+6 真实验收

北京时间 2026-11-21。执行规格：`/home/xiu/xiuxian-7plus6-goal-plan-20261121.md`，采用其 02:50 更正：默认第一重，不执行旧高重或爬重方案。通过只认服务端通关/已领奖，目标仍是两主号 26 格及连续三个自然日。

## 接手与证据

- 08:42 核对 Claude PID 1500141：会话元数据为 `idle`，最近 assistant 回合已 `end_turn`，无子命令、无生产/战斗文件句柄。进程存活本身不等于另一个写入者，之前因此停工的判断过严。接手时生产 HEAD `764c6bfd`，仅题库未提交。未杀掉其他会话。
- 完整阅读执行规格、第 10 节列出的交接/审计、fanren 研究与本地 3D 结论；复核官方指令索引及 0.2.17 release。指令索引已改为动态加载 `/staticweb/guide/commands-v1.json`，不以空 HTML 的搜索结果判断玩法不存在。
- 本轮私有证据目录：`/root/xiuxian-tailnet-20261121.zSAVAf/`。SQLite 在线一致性备份 `chaogu_state.db`、API 配置前备份 `before-api-config.db`，均以 SQLite backup 从只读源生成。题库 SHA256：`5f82447d6ba00f5c555a0ec348fcb106aee3a92bc2a2fd09711d30cc3e634103`。
- 09:07–09:12 左右，依次对四号做九项奖励和斗法档案只读探针；均校验实际 playerId，无开局。原始脱敏结果在 `observations/<identity>-qingxi_rewards.json` 和 `<identity>-qingxi_trial_profile.json`。

## 主号 26 格事实表

以下以 15:45 服务端探针为基线，补入 21:04 WA 新鲜九项状态。“待验收”表示未通过，非角色限制；当前 **0/26 通过**，尚未作角色限制结论。七副本两号均未解锁且无 run（WA 21:04、吧唧 15:45）；WA 麦田、竹林各 `used=1, remaining=2, activeRun=null`，两场地均当日战败停；吧唧两场地基线仍 `used=0, remaining=3, activeRun=null`。

| 槽位 | wa 8659059191 | 吧唧 301299112 |
| --- | --- | --- |
| 月殿血誓 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 血色禁地 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 南门守城 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 护民出关 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 血祭神坛 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 试傀场 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 剑冢 | 待验收：主线未解锁 | 待验收：主线未解锁 |
| 麦田 1 | 失败：run `2PKPHYu20zxxZCw8` 第一重已关闭，服务端仅确认三波，未通关；used=1 | 待验收：今日未开局 |
| 麦田 2 | 待验收：今日未开局 | 待验收：今日未开局 |
| 麦田 3 | 待验收：今日未开局 | 待验收：今日未开局 |
| 竹林 1 | 失败：run `GIKgjPBGp685Nvdf` 一重首领波战败，前三波已确认，局已关闭；used=1 | 待验收：今日未开局 |
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

阶段 0 曾满足出口，阶段 1 的角色与七项前置已核对。阶段 3：两号旧账以及 wa 新接敌距离拒绝均已按记录与新鲜回执复核，当前无主号青溪不确定占用。阶段 2/4：wa 麦田第一重已真实尝试，首领战败、同波重试后超时，不通过；须先完成零重试整改和战术分析。主线当前仍受野外低血量制约，七副本未解锁。阶段 5/6 未开始，整批与主线调度均关闭。

1. 已完成 Tailnet 无 TG 登录与边界验证。
2. 原两条 operator_release 和新接敌距离拒绝复核已完成；保留原 operation 证据，不重发旧写入。
3. 先修复正常安全回血与战败停规则；新的真号运行暂缓，未知不重试。
4. 补齐两场地/七副本打法、低画质 FPS、时间预算和本地资源缓存接入；符合阶段出口才进入单场验收。
5. 等待窗口按规格处理婉心失败生命周期、禁言停发、猎妖旧错误、通知统计和 HTTP 迁移盘点。

## 15:45 续跑记录

- 当前生产主线仍为 `a3fd99e4`（待继续修复的文件未发布）；健康观察器、watchdog 和 `xiuxian.service` 活跃。`data/quiz/quiz_bank.json` 是既有用户改动，保持不纳入提交。
- 新鲜只读验收：wa 麦田当日 0/3、竹林 0/3，均无 `activeRun`；七副本仍未解锁；`qingxi_rewards.enabled=false`。
- wa 的旧 `field-engage-mwpq9hnk-m-jsm4jn` 已通过正常 refresh 归档为 `confirmed_field_engage_distance_rejection`，新鲜 status 确认对象存活、无尸体且飞行关闭。随后 qx_q11 新 run `quest-796099d258594a32ada8020b8400ba85` 因低血量安全返回；其中 `field-release` 的完整 HTTP 409 `quest_too_far` 回包被标记 `rejected_noop`，没有遗留 pending。q11 `arrows=2` 未增加，任务状态 fresh，pending=0，飞行关闭，不计通过。
- wa 麦田首层 run `2PKPHYu20zxxZCw8`：真实浏览器在 15:18:39 开局。服务端逐波回执确认第 1–3 波（`cleared=2`）；第 4 波两次尝试后在 25 分钟墙钟上限停止，服务端最终 `activeRun=null`、`server_cleared=false`、`rewarded=true`、`used=1/3`。部分奖励为灵石×378、法则碎片·火×1、回灵丹×2。该格记录为失败/未通关，不是“通过”；麦田今日停止继续尝试。原始证据位于 `/root/2026/qingxi-trial-m19-wa-canary-20261116/.runtime/8659059191/c4199bdb8911480da51481ce439de757/`。
- 15:37 首次倒地后，runner 自动同波重试一次，违反本规格的战败当天停规则；主控制器未及时终止，继续至超时才退出。本次按红线事件报告，不以同一 run 未额外扣配额豁免。`qingxi_rewards` 与 `qingxi_quests` 总开关均确认关闭，未启动竹林或其它新的真号操作。零重试修复把 `--retries` 从 1 改为 0，手动与自动入口都有命令级回归；发布前仍需完成检查。
- 野外战斗不会从背包消耗九转还魂丹；官方进入野外时血瓶计数为 0，离线不恢复气血。回血候选已从生产工作树撤出，保存为 `/tmp/qingxi-health-recovery-candidate-20261121.patch`；候选存在洞府与坊市场景混淆、父子期限不足的问题，不能视为实现或发布完成。
- 本 run 奖励通知只有一次确认投递：journal `TG_NOTIFICATION_DELIVERY` receipt `5fbd5d92173448b485ffa4de4ff6e8d6`，15:43:32、outcome=confirmed。该证据只确认通知送达，不证明通关。
- 下一步：完成零重试隔离回归和事件整改审查；新真号运行暂缓。回血与首领打法在隔离工作树修复，保留麦田当天失败状态，26 格仍为 0 通过。

## 16:48 零重试整改验证

手动与自动入口现在均向固定版本 runner 传 `--retries 0`，仍只开一场、固定第一重。相关 166 项回归通过；隔离工作树恢复旧 runtime 后，手动与自动入口两个测试均按预期失败，证明用例会捕捉原重试配置。全量隔离回归 `18858 passed, 1561 subtests passed in 554.43s`；全量 Ruff、Python 编译、三个相关 Node 语法检查与 diff check 通过。发布前无浏览器 worker、发送队列为空，两个青溪总开关均关闭。此验证不改变失败事实与 0/26 结果。

## 17:38 原生回血及战败停跑发布前验证

零重试 `8cc5d07b` 已于 16:49:30 发布，复核三服务活跃、发送队列为空、无浏览器 worker。低血恢复与超时掩盖战败的后续修复 `f88b15f1` 已合入待发布：主线仅在当前原生安全坊市观察到气血 ≥80% 与绘制继续后启动；野外落点先正常回洞府、核验新回执再入坊。父子进程共享 4450 秒最长预算，不改气血、时间或坐标。缺失控制器、暂停/覆盖层、鬼魂、未决请求及不明场景阻断。试炼保留原始 timeout 和真实倒地数量，同身份同场地当日战败同时阻止手动和实际启动；两个场地各自保留复核暂停。详见 `qingxi-native-health-defeat-20261121.md`。

最终隔离全量回归 `18914 passed, 1561 subtests passed in 546.46s`，全量 Ruff、Python 编译、注入助手及策略/整批 JS 语法、diff check 通过。回血撤修复 6 例失败，试炼旧运行时代码下 5 例业务回归失败。生产记录的只读副本演算恢复 WA 麦田 `deaths=1` 及 confirmed_defeat，重复对账不再修改，也不误挡竹林；未写生产 DB。实际回血尚未验收，26 格仍 0 通过，整批和任务调度保持关闭。

发布备份与全量日志：`/root/xiuxian-qingxi-7plus6-release-20261121/health-defeat-1725/`。下轮只手动验 WA 主线恢复，今日麦田禁跑；不以本修复测试作为通关。17:28 只读调度核对无已知近期组队活动/官方定时冲突，WA 裂缝仍为 22:27:33；普通宠物与小世界计时只作并行观察，未发现会改变原生青溪场景或配额的证据。

## 18:01 WA 原生回血实机与 qx_q11

- `f88b15f1` 于 17:38:59 发布后，重启恢复 24 个身份，主服务、safety watchdog、health observer active，发送队列 0。重启对账从原 WA 麦田目录恢复同一 run 的 `deaths=1` 与 longwind confirmed_defeat；竹林当天未被错误挡住，原试炼通知仍为一次。
- WA 任务 job `dfa4c6c5992743209dfa9805334a4b6d` 从洞府外野战位置开始。新鲜当前原生气血 **4525.8/15086**（约 30%），流程正常 `returnCave`，核验新 `home.verified=true`，重新通过助手入坊。安全坊市保持当前 runtime/combat 一致、未暂停/非鬼魂、无飞行/交战/未决写入。原生 draws 前进；气血由 **4525.8** 增至 **12445.95/15086**（约 82.5%），历时 **255.5 秒**，且有正向气血增长，随后才记录 `mainline_started_at`。该 run 最终气血回满。
- qx_q11 箭簇由 **2/10** 连续增加至 **10/10**，服务端状态为 `rewarded`。共 17 个有回执的野外结算；单个敌人多次重建时不重复计入不同任务奖励。任务服务端刷新新鲜，pending 内存与磁盘均空，战斗 inactive，原生飞行关闭；退回安全洞府 `[2.55,0,0.7]`。任务调度状态恢复 `waiting_gameplay, gameplay_uncertain=false`，下一 qx_q12 为 active。
- 此运行历时约 20 分钟，虽未超过新 4450 秒父预算；qx_q12 接取后因 `[57,1,5]` 无步行路线结束。按原流程安全返回，q12未产生新进度/奖励，当前无在途 worker 或 Chromium。证据见 `/root/xiuxian-qingxi-7plus6-release-20261121/health-defeat-1725/native-health-live-evidence.json` 和原始目录 `/root/2026/qingxi-trial-v13-20261105/.runtime/8659059191/quest-dfa4c6c5992743209dfa9805334a4b6d/`。这次实机通过**只验收低血安全恢复闸门及 qx_q11 真实交付**，不代表 qx_q12 或任何 7+6 奖励槽通过；试炼麦田战败事实不变。
- 下一步先查 qx_q12 区域寻路失败成因，再在没有未决、身份和场景全 fresh 时续推 WA 主线；不重跑已领奖 qx_q11，不尝试 WA 麦田第二场。吧唧仍在正常闭关次轮，选择下一场需先看服务器零点前的剩余时段与活动占用。

## 18:30 WA qx_q12 续跑

- 18:10 左右经新鲜状态确认 WA `waiting_gameplay/gameplay_uncertain=false`，qx_q12=`active`、ferrules=0/8；原三个任务接受均有独立成功 operation receipt，不重复 accept。无 worker、pending、已知组队运行或零点前高优先级活动，遂从洞府入口只续同一 q12。
- 新 job `quest-085c33dee8624ee3a7949a82d86942d1`。低血门直接通过：入坊 HP 已满。原铁匠屋檐导航点 `[57,1,5]` 未重现；正常寻路后服务器确认 qx_q12 ferrules **4/8**。之后 `长枪手` 单体战斗达到固定 5 分钟目标时限，战斗没有取得胜利；10 笔击杀结算均 `ok=true`，最近敌人 `lantern:086`；无失败/拒绝结算。这与回执等待超时不同，但原始结果没有留存超时时当前敌人 HP/距离/leash 状态，无法判断是目标返家、攻击动作无效或其他原因。
- 现已安全返回洞府 `[2.55,0,0.7]`，最终 fresh task status true、pending=0、stored pending empty、combat inactive，flight check true。WA record 为 `waiting_gameplay/gameplay_uncertain=false`，新鲜观测 q12 active 4/8，q13/q14 仍 active；试炼长风 confirmed_defeat 同一天禁跑保留不变。没有继续重试同一敌人或接受 q12。
- 为下一次单场续跑已准备一个**隔离的诊断候选**：只在原生 5 分钟战斗超时时记录所请求/实际目标、HP、位置、engaged/returning、home/leash、玩家距离、战斗指标及匹配目标最近 4 个结算；不变更 5 分钟时限、打法、请求或回执。Node 夹具和源版本锚点 2 项通过、Ruff/diff 通过；尚未全量测试、提交或生产发布。因此下次真号仍需遵守现有生产策略；若原样再次超时，停止该身份的该任务 combat 写操作并重新采集证据。
- 原始任务证据 `/root/2026/qingxi-trial-v13-20261105/.runtime/8659059191/quest-085c33dee8624ee3a7949a82d86942d1/`；脱敏摘要 `/root/xiuxian-qingxi-7plus6-release-20261121/health-defeat-1725/qx12-live-evidence.json`。这只是 q12 部分进度，不计七副本或试炼通过。下一窗口先完成隔离诊断补丁全量回归与发布判断，再依据 fresh 状态续 q12，不重复任务领取；0/26 验收结论不变。

## 19:08 qx_q12 战斗超时诊断补丁发布

- `a0a07361` 在 pinned upstream task worker 适配层及纯 Node 诊断夹具增加 timeout-only 本地证据：被请求/实际目标身份、血量、状态、当前与 home 坐标、leash 半径、玩家距离、原生 combat metrics 和严格匹配的最近结算。它只在既有 5 分钟目标时限结束时取样并抛出原来同文错误；不改变计时、目标选择、攻击/走位、请求/结算或停止行为。
- 夹具+source pin 2 项通过，Ruff、Python 编译、注入脚本与策略 JS 语法通过；合入前隔离全量回归 **18915 passed, 1561 subtests passed, 549.71s**。`data/quiz/quiz_bank.json` 保持为唯一生产未提交运行时改动。
- 发布前通过认证 UI 确认队列为空、无青溪浏览器；一致性 SQLite 备份为 `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855/pre-restart-state-final.db`。待仅重启主服务并确认三服务恢复、队列/worker/status 与开关原值后，续 q12 使用此诊断。q12=4/8，q11=rewarded；试炼开关仍关闭，WA 麦田当天仍 confirmed_defeat；26 格仍 0/26。

## 19:42 WA qx_q12/13 续推及服务重启核验

- 已提交 `a0a07361` 超时现场诊断与 `5509f3dd` 发布核验记录并推送 `xiuxian-mian/main`；19:09:05 仅重启 `xiuxian.service`。重启后主服务 NRestarts=0，24 身份恢复，safety watchdog/health observer active，queue=0；青溪两个主开关仍关闭。试炼长风自动暂停及 4/8 q12 fresh 状态保留。
- WA job `quest-f7c9a0e4bbbf497eb92fba3aee987d37` 从安全洞府继续已接受的 q12，满气血启动。没有重复接取 q12，正常战斗与 corpse-pickup 最终让 q12=8/8 ready 并经正常 turn-in 变 rewarded；对应任务接受 run_id 与回执保留在运行状态。此次无 `field_fight_timeout`，没有作超时诊断结论。随后的 q13 也收到药匣/铜钥两份成功拾取回执，turn-in `3dfca8dd-9dd6-4e99-96b5-74b8bd189520` 成功、q13=rewarded，奖励灵石×478、回灵丹×2、清灵草种子×2。
- q14 已 active，但符芯 0/15、阵盘 0/2。末端试图继续御空进入竹林时，多条正常原生御空航线失败，助手无可用落点后按原流程返回洞府；最终 server_checked=true、pending=0、stored_pending=[]、战斗 inactive、洞府落点 `[2.55,0,0.7]`、flight closure 新鲜读回，身份记录 `waiting_gameplay/gameplay_uncertain=false`。证据 `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855/qx12-completed-evidence.json`、`qx13-live-evidence.json` 和原始 `/root/2026/qingxi-trial-v13-20261105/.runtime/8659059191/quest-f7c9a0e4bbbf497eb92fba3aee987d37/`。
- 目前主线 q11、q12、q13 为 rewarded，q14 未完成；这不会直接使任何每日有奖副本槽变成通过。WA 麦田仍保持当日确认战败停跑，竹林试炼今日未开；两个青溪自动总开关仍关闭，26 格仍 0/26 通过。下一轮先用 fresh status/flight 和有效窗口续当前已接受 q14，不重跑 q11–q13、不重复奖励交付。

## 20:08 WA qx_q14 当日有界续跑

- WA job `quest-5a3ed717e6e6427b801845c58d99aac6` 的正常 q14 流程在安全区域一次任务 worker 内运行至本任务 900 秒时限。新鲜最终任务状态成功、内存和磁盘 pending 空、战斗 inactive、返回洞府；flight closure 也有 fresh check。未超父级 4450 秒总预算，自动化记录 `phase=bounded_stop/gameplay_uncertain=false`，故可安全续跑。
- q14 服务端确认符芯由 **0 增至 14/15**；阵盘仍 **0/2**，q14 仍 active。无 q14 turn-in，不计算奖励或通过。损伤归零前 HP 始终回满/安全；当前没有正在运行的 worker。
- 本次 q14 job没有五分钟目标超时，不会有 `field_fight_timeout` 快照；先前失地→敌人计数同步→飞行追击等 27 笔场战结算已确认成功。结束位置洞府 `[2.55,0,0.7]`，`server_checked=true`、pending=0、q14 fresh仍差一个符芯和全部阵盘目标。运行证据 `/root/2026/qingxi-trial-v13-20261105/.runtime/8659059191/quest-5a3ed717e6e6427b801845c58d99aac6/result.json`，快照 `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855/q14-live-evidence.json`。
- 下一自然日继续 q14 时重新查看已完成数量和场景，不从已获奖励倒推目标；只处理 remaining=1 符芯以及阵盘2枚。麦田本日战败暂停及竹林 trial次数状态无变化。总体26槽仍0/26通过。

## 20:25 WA qx_q14 交付

- WA q14 continuation job `quest-679199afcc8142a390101cfc029a2bae` 使用 fresh 原生入口、满血开启，服务器累计符芯 **15/15**、阵盘 **2/2**；之后脚本按任务目标返回交付。q14 最终服务端状态 `rewarded`，2026-11-21 20:25 新鲜 task status 确认，pending 清空、战斗关闭、位置安全洞府 `[2.55,0,0.7]`、flight closed。下一 active gap 是 q15「灯尽契散」拾取原契血印 0/1。证据 `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855/q14-followup-live-evidence.json` 和原始 job 路径。
- 本次途中多笔原生御空航线受阻，最终在 q15 长途移动目标 `[9.9,1,-1]` 处找不到步行路线并返回洞府；没有未决写请求或未知结果。不在本次追加一轮重试。它覆盖完成 q14，不覆盖日奖励槽/试炼槽。WA 当前 q11–q14 为 rewarded，q15 active；吧唧当前仍 q11/q12 rewarded、q13/q14 active，尚无 q15 进度。
- 当前时间约 20:25。WA 次日元婴计时在约 22:04 CST，WA 天星裂缝 22:27 CST；q15 下一次 30 分钟以上运行可能撞上。等待该活动窗口安全过去后，再 fresh-check WA Q15 并推进吧唧 q13 采集，维持单浏览器。
- 本次之后仍没有真实每日副本领奖或 6 次试炼通过证据；26 格事实表仍 0/26，所有清溪自动化开关保持关闭。

## 20:45 WA q15–q19 交付，竹林一重单场开始验证

- WA 浏览器 `quest-9725ae94887047dba351f400b66d7209` 验证身份和满血入坊，原契血印拾取 `efa7cd46-8cae-43e5-89b6-e0a70a6258e8` 成功 1/1，原生 q15 交付 `eb24f338-c1dd-4da8-900a-60e6c67fa1d6`，服务端奖励灵石×669、增元丹×3、回灵丹×2。无五分钟野战超时。
- 浏览器停止后返回洞府 `[2.55,0,0.7]`，任务状态 fresh，战斗关闭，内存和磁盘 pending 皆空，worker 与 Chromium 已清理，宿主复核飞行关闭。q16 浏览器最终为 `active`，随后宿主按正常任务流程交付 q16–q19，四项均有各自回执。20:43:37 发出五项交付汇总一次，宿主 `notice_state=sent`。q20 剑丝 0/6，q21 旧簪 1/1 待奖励选择，q22 locked。脱敏证据 `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855/q15-q19-live-evidence.json`。
- 更正上节时间与路径：原 `[9.9,1,-1]` 是 q14 交付点；q15 后来在上述浏览器真实交付。WA 元婴排期实为 11-22 **03:06:59**，裂缝仍为 11-21 **22:27:33**，准备窗 **22:17:33**。本次开局前无排定官服发送、团队活动或在途发送，距裂缝准备超过 90 分钟。
- 20:45 服务端九项只读核对：WA 七副本皆未解锁且无 run；麦田 `used=1/3` 已关闭，保留 `confirmed_defeat`，当天不试；竹林 `used=0/3`、`activeRun=null`，无当日战败。20:45:30 经认证入口提交一次竹林一重有奖验证，job `fdd4867cdecd484f96c913db285d6f74`，预留 35 分钟，尚未确认本局结果。仍 **0/26**，未进入三日观察。两个青溪自动调度开关维持关闭。

## 21:04 WA 竹林一重真实战败与安全关闭

- 同一 job 20:49:06 通过场景、位置恢复、原生动作与举盾预热，以第一重正常有奖开局 `GIKgjPBGp685Nvdf`。只有一笔 start，`retries=0`、`deathLog=[[3,264]]`、`deaths=1`；前三波服务端确认（cleared=2），首领波战败。本次零重试保护实机生效，不是通关。
- 战斗墙钟 **752.72 秒**、原生时间 **264.24 秒**（约 2.85:1），未等到 1500 秒上限；服务端 `run.status=defeated`、`activeRun=null`、竹林 `used=1/3`。部分奖励灵石×373、法则碎片·木×2、回灵丹×1；确认倒地后即结束，未消费第二次配额。worker/Chromium 已清理、无 cleanup_error/unknown_request。
- 宿主持久化竹林及麦田各自 `defeats_by_arena`，两个场地今天均停，旧麦田暂停没有被新局覆盖遗失。21:04 九项只读核对再次确认两场地各 used=1 且关闭，服务端飞行关闭；未放宽 held 或战败规则。通知恰一次，bot 确认回执 `451d61b890424e75af6e308b2d108497`。
- 证据摘要 `/root/xiuxian-qingxi-7plus6-release-20261121/fight-timeout-diag-1855/bamboo-first-tier-defeat-evidence.json`；原目录 `/root/2026/qingxi-trial-m19-wa-canary-20261116/.runtime/8659059191/fdd4867cdecd484f96c913db285d6f74/`。原生读取样本与首领截图保存在摘要同级。新斗法档案有六槽，包含朱雀环，后续分析不得套用旧五槽假设。
- 26 格仍 **0/26**，今天不再尝试 WA 双试炼，转离线打法分析和主线。21:04 通过认证配置明确 q21=`endure`、q25=`elixir`，分别选回灵丹×2/×3，服务连续战斗携带；仅登记，尚未据此领奖。两个自动开关维持 false，q31 不可更改宗门选择仍未填。旧配置备份 `quest-reward-choices-before.json`。
