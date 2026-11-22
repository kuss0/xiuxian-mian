# 青溪不经浏览器的协议执行研究

用户要求研究 HTTP/WS 直接执行，减少软件 3D 渲染负载。
本轮不切换真号到新战报生成器；原有 HTTP 模块继续正常执行。

## 已有真实证据

吧唧 q15 用原生运行时完成战斗和掉落，浏览器退出后，同一 run_once
的 run_protocol 直接通过 HTTP 完成 q16/q17/q18/q19/q21。
第二轮又通过 HTTP 交付 q20 和完成 q22。
接取/交付、普通采集与 NPC 访问本来就能脱离浏览器执行。
证据 baji-q15-q21-completed.json、原任务 result.json 与宿主 receipts；
代码 model/features/qingxi_quests.py:run_protocol/mutate/plan_next。

| 能力 | 官方/现有通道 | 本轮结论 |
| --- | --- | --- |
| 身份初始化 | Telegram RequestMainWebViewRequest + MiniApp HTTP | 现有路径无需渲染器；仍验证原 player ID |
| 普通任务接取、交付和状态 | /api/miniapp/xianxia-dwelling/quests | 已有直接 HTTP 真实成功回执 |
| 普通节点采集、访问 | quests 的 gather-start/gather-finish/visit 等 | 现有 HTTP 段可执行；带期限/原生租约的目标仍逐项处理 |
| 野外战斗授权/掉落 | field-engage、field-cast、field-defeat、corpse-* | 是 HTTP，但战斗动作和命中仍由客户端计算并核对 |
| 七个单人副本 | 各资源 status/start/end | end 携带原生波次、采药、命中、存活和机制报告；请求存在不等于可省略战斗 |
| 3+3 试炼 | trial 的 status/start/checkpoint/end | 仍需原局波次和通关证据，旧 HTTP 上报不能充作原生验收 |
| 多人连接与五人副本 | /ws/miniapp/xianxia-dwelling/town、/raid | 有 WS 战斗，但玩法与当前单人 7+6 不同，尚无奖励/配额等价证据 |

## 官方源码依据

本轮在线 dwelling-field-combat.js SHA256
57c2e83320a6ae3525f1d38d4fa08c20d39fdc400d7c98716c89bd94f9399081。
fieldCall 在 fieldCasting=enforce 时先 flush 施法事件；reportFieldDeath
提交实际伤害、命中及位置。bloodReport 要求波次、药草、击杀、受击、存活。
同文件工厂依赖 Three 场景、角色动作、地面/碰撞、战斗 reference 和 DOM。
dwelling HTML 的 raid/session + WS/raid 属于五人副本。

旧 model/features/changfeng_trial.py 的 _trial_report 按报告档位生成指标；
这段代码确实能发 HTTP，但它不是本轮真实浏览器战斗记录，不用它替代
当前计划要求的命中、配额、通关和三日验收。

## Node 验证和下一步

官方 dwelling-cast-transport.js 自带 module.exports，原文件已在 Node
加载，并通过合成 ack 验证保留事件直到全部确认。原版 treasure-rules.js
与战斗工厂也可加载；未构造完整场景/角色依赖，未证明可在 Node 打完整场。
这次探针没有发游戏请求，证据 protocol-node-probe.cjs/.json。

可继续研究保留官方 Three 数学/动画/碰撞与原始施法状态、去掉显示渲染，
然后逐段与真实客户端动作和报告做一致性回放。只有传输模块能运行，
不能声称整个战斗已移植。先迁现有协议可做的步骤；当前减压采用远端
Chromium，让原生战斗继续得到真实证据，而不另写战报或机制计算器。

## 原生控制器在 Node 的进一步验证

本轮用原版 Three r160、treasure-rules、cast-transport 和 field-combat，
在 Node 创建真实 Three Scene/Group/Camera，不创建 WebGLRenderer。
DOM 仅提供原 HUD 需要的空壳；地面为明确的合成平地，控制器禁用。
原版控制器成功初始化、40 次空闲 step 调用、stats 和 dispose 均正常。
原生 start 在加载人物动作时明确要求 createLongWindCombatDirector，
未把缺少的动画或骨架改成自写判定。零网络请求、没有战斗或奖励上报。
证据 protocol-native-engine-probe.cjs/.json，含原文件 SHA256。

从当前浏览器已经下载的同源缓存只读提取原版 longwind-combat-v1.js，
没有额外向游戏接口发请求。该文件版本 32，SHA256
05b7b4d20a3946c311cb0dbbc7ed89df2e8d01e1851049ea93f1e7b02b286324。
它打包了原生 Animator、Actor、bakedAnim、modelSkin、sweepBladeVsCapsules，
确有保留原判定而去掉显示渲染的继续研究方向。
下一关是原版 GLTFLoader、人物模型/动作包、场景地形与碰撞、施法记录
一致性；空闲控制器初始化不能称为已用协议完成战斗。
