# 天星宗 Lab 深度测试审计（2026-06-29）

## 范围

本轮审计只验证天星宗 lab 链路，不扩大生产主动发送。目标是覆盖 `观命 -> 定命 -> 推命 -> 改命 -> 下游路线放行`，以及普通闭关攒天机点与深度闭关互斥接管。

## 场景矩阵

- 真实文案：`天机盘`、`观命`、`定命`、`推命`、`改命`、`消劫`、`推命命中/落空`、`改命回天`、天星普通闭关成功、合气丹成功。
- 文案轻微变化：`推下一段命数` / `推下了一段命数`，`预留了一次改命回天` / `预留一次改命回天`，普通闭关冷却文案有无空格均可解析。
- 未知文案：包含司命盘但无明确动作的文本只记录为 `未知天星宗文案`，不改推命/改命状态，不放行下游。
- 吞回复：时间线战略命令已发送但没有真实回复时进入 `ack_timeout`，只安排 `.天机盘` 校准，不放行下游。
- 迟到回复：超时后收到推命真实回复，不立即放行；必须查盘确认，随后进入重算，再由新时间线放行。
- 陈旧回复：早于发送时间的回复不确认当前步骤，不释放路线。
- 普通闭关攒点吞回复：`.闭关修炼` 回复超时后只查 `.天机盘` 校准，不重复发送 `.闭关修炼`。
- 普通闭关迟到回复：迟到的闭关成功且含 `【推命命中】` 时进入校准阶段，记录天机值收益，不盲发下一轮。
- 深度闭关互斥：深度闭关即将发起时先走天星 gate；天星接管则延后深度闭关，不接管才放行深度闭关。
- 强行出关：`.强行出关` 仍归深度闭关模块发送；总结文案中的普通闭关调息时间回写天星攒点冷却。
- 合气丹：只有配置允许时才发送 `.服用 合气丹`；成功回复将攒点状态置为 ready。
- 安全边界：所有发送仍走 `send_game_command`、`action_guard`、runtime reply family、module manifest，不绕过全局锁或 watchdog。

## 验证

命令：

```bash
.venv/bin/python -m pytest tests/test_tianxing.py tests/test_phaseful_summaries.py tests/test_module_manifest.py tests/test_safety_watchdog.py tests/test_message_evidence.py tests/test_real_message_replay.py tests/test_app_scheduler_contract.py tests/test_ui_dual_track.py -q
node --check model/web/static/js/module_cards_ui.js
.venv/bin/python -m compileall -q model tests
git diff --check
```

结果：

- `302 passed, 51 subtests passed`
- `node --check` 通过
- `compileall` 通过
- `git diff --check` 通过

## Live 观察前置状态

- 小耳朵图图：`3823558636 / @tutuerduoxiao / 天星宗`
- `tianxing_enabled=1`
- `tianxing_auto_config={}`，即天星模块开启但普通闭关攒点、推命/改命主动策略仍保持默认关闭/dry-run 安全态。
- live 观察从本审计通过后重新计时。

## 2026-06-29 09:15 复核

按新增要求重新核对天星 lab 覆盖面：真实文案、轻微文案变化、未知文案、吞回复、超时回复、迟到回复、陈旧回复、普通闭关攒点回复异常、合气丹、强行出关回写、深度闭关互斥、安全锁顺序与下游放行均已有用例或审计约束。

复跑结果：

- `tests/test_tianxing.py -q`：`50 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放宽集：`302 passed, 51 subtests passed`
- `node --check model/web/static/js/module_cards_ui.js`：通过
- `compileall model tests`：通过
- `git diff --check`：通过
- `tools/safety_watchdog.py --once`：`watchdog ok`
- `tools/health_observer.py --once`：`health_observer ok`

下一步：以 `3823558636 / 小耳朵图图` 为目标启动 3 小时观察；若出现天星异常，先暂停目标身份天星模块，再回 lab 修复、复测、恢复观察。

## 2026-06-29 09:50 现场监测修复

小耳朵图图开启主动天星配置后，现场出现：

```text
[tutuerduoxiao] 🧯 安全锁拦截：.定命 贪狼｜天星定命 本轮已发送 1/1 次，等待结果或人工处理
```

同时真实回复显示：

```text
此命星并未在你今日观命结果中显化，请先 .观命。
```

根因：

- 安全锁本身生效，拦住了重复定命；问题在天星状态机没有充分对接安全锁与发送队列。
- 天星时间线在发送期间没有先落 `sending` 占位，深度闭关 gate / 自动调度 / 下一轮时间线可能再次尝试同一步。
- `send_game_command()` 返回空时，天星侧将其视作硬失败，容易形成可重发状态；对战略命令应先等待真实回复或查盘校准。
- `.天机盘` 的 `今日可选命星` 不能等同于 `.观命` 的当日可定命星；未显式观命时不应直接 `.定命`。

修复：

- 时间线执行加身份级 async lock。
- 战略动作发送前先写入 `sending`，其他调度看到 `sending` 只等待，不重发。
- 发送未返回消息 ID 时进入 `ack_timeout -> .天机盘` 校准，不直接变成可重发失败。
- `timeline_enabled` 开启时，普通天星自动调度不再直发 `.定命/.推命/.改命`，战略动作统一由时间线授权。
- 区分 `available_stars_source=panel|observe`；只有 `.观命` 来源允许自动定命，`.天机盘` 来源先补 `.观命`。
- 解析“此命星并未在你今日观命结果中显化，请先 .观命。”，清空可定命星并让时间线回到观命重算。

复测：

- `tests/test_tianxing.py -q`：`55 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放宽集：`307 passed, 51 subtests passed`
- `node --check model/web/static/js/module_cards_ui.js`：通过
- `compileall model tests`：通过
- `git diff --check`：通过
- `tools/safety_watchdog.py --once`：`watchdog ok`
- `tools/health_observer.py --once`：`health_observer ok`

下一步：重新开启 `3823558636 / 小耳朵图图` 天星主动配置，并重新计时 3 小时持续监测。

## 2026-06-29 10:08 监测补丁

固定版监测时没有再出现安全锁撞击，但发现 `.天机盘` 返回未定命且带 `今日可选命星` 后，自动调度仍按普通已观察状态睡眠 6 小时，未及时补 `.观命`。

修复：

- 未定命且 `available_stars_source != observe` 时，`auto_next_time` 调整为 60 秒内，驱动自动补 `.观命`。
- 增加用例覆盖 `.天机盘` 未定命后必须短期补 `.观命`，不能按 6 小时状态退避。

复测：

- `tests/test_tianxing.py -q`：`56 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放宽集：`308 passed, 51 subtests passed`
- 静态检查、compile、`git diff --check`、watchdog、health 均通过。

## 2026-06-29 10:11 生命周期修复

重开小耳朵图图天星后再次出现：

```text
[tutuerduoxiao] 🧯 安全锁拦截：.天机盘｜天机盘 本轮已发送 1/1 次，等待结果或人工处理
```

根因不是发送间隔过短，而是模块启停生命周期没有闭合 action_guard 会话：

- 关闭天星时只清理了 `tianxing_observation`、`tianxing_timeline_state` 和 pending task。
- `action_guard_sessions` 中旧的 `tianxing_set_star`、`tianxing_panel` 仍保留。
- 再次开启天星后，时间线按新状态发送 `.天机盘`，但发送层仍认为上一轮 `.天机盘` 已发且未闭合，于是安全锁拦截。

修复：

- `action_guard` 增加按模块解析并关闭 action keys 的公共能力，来源为 `module_manifest.reply_families -> action_guard.FAMILY_TO_ACTION_KEYS`。
- `control._clear_pending_tasks_by_commands()` 在清 pending 时同步关闭同命令的 action_guard 会话，补齐通用生命周期缺口。
- 天星禁用时按模块完整关闭自身 action_guard 会话；天星启用前也执行一次模块级 reset，处理旧版本已遗留的现场会话。
- 保留远端 CD/运行态锁：远端 block 不直接删除，只归零发送尝试，避免误放行深度闭关、元婴等真实 CD。

复测：

- `tests/test_control_bool_coercion.py tests/test_message_evidence.py tests/test_tianxing.py -q`：`107 passed, 54 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`359 passed, 56 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

下一步：重启服务加载修复，重新开启 `3823558636 / 小耳朵图图` 天星，观察时间调整为 6 小时；如再次出现天星安全锁/看门狗异常，先关闭该身份天星，再回 lab 修复并继续计时。

## 2026-06-29 10:30 被动收件箱闭合修复

重新监测时发现 `.天机盘`、`.观命` 已收到真实回复并写入 `tianxing_observation`，但 `action_guard_sessions` 仍保留 `tianxing_panel` / `tianxing_observe`。

补充根因：

- 天星回复没有走 `app._handle_routed_reply_event()` 中的显式 `handle_*` 分支。
- 天星状态由后置 `passive_inbox.handle_passive_module_card()` 更新。
- app 先看到 `handled_any=False`，不会调用 `close_action_guard_by_family()`；passive_inbox 更新状态后也没有补关 action_guard。
- 因此“回复到了、状态已变、发送层仍认为本轮未闭合”会持续到 TTL，下一轮状态查询容易再次撞安全锁。

修复：

- passive_inbox 在通过 reply family 成功更新模块状态后，同步关闭对应 action_guard family。
- 排除 `concubine_heart` 多轮非终态链路，避免心劫中间轮次被提前闭合。
- 增加天星用例：造出 `.天机盘` action_guard 会话后，由 passive_inbox 处理真实天机盘回复，断言 `tianxing_panel` 被关闭。

复测：

- `tests/test_tianxing.py tests/test_message_evidence.py tests/test_control_bool_coercion.py -q`：`107 passed, 54 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`359 passed, 56 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

处理流程：

- 已按监测约定先关闭 `3823558636 / 小耳朵图图` 天星。
- 重启服务后重新开启目标天星，6 小时监测重新计时。

## 2026-06-29 10:36 观命后推进修复

再次监测时，`.观命` 已正常发送并收到回复，且 `tianxing_observe` action_guard 已被 passive_inbox 闭合。但观察到新问题：观命成功后 `auto_next_time` 被推到 6 小时后，链路停在“已观命、未定命”。

根因：

- `apply_tianxing_passive()` 对“无固定命星”的状态只在缺少 `observe` 来源时短退避。
- 当 `.观命` 成功后，`available_stars_source=observe`，逻辑进入普通状态查询退避，导致不会及时进入定命/时间线规划。
- `timeline_enabled=true` 时，`run_tianxing_scheduler()` 不直发定命/推命/改命是正确的，但它也没有在 farm window 内主动调用时间线规划器，导致只有深度闭关/野外/裂缝等下游模块触发时才会推进。

修复：

- 只要尚未定命，`apply_tianxing_passive()` 都把下一次自动调度压到 60 秒内。
- `run_tianxing_scheduler()` 遇到 `timeline_required` 且当前处于天星 farm window 时，主动调用 `run_tianxing_timeline_scheduler()`，由时间线授权定命/推命/改命。
- 仍然保持普通调度不直发战略命令；没有 farm window 时继续退避，避免全天候乱推命。

复测：

- `tests/test_tianxing.py -q`：`58 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`361 passed, 56 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

下一步：重启服务、重新开启 `3823558636 / 小耳朵图图` 天星，6 小时监测重新计时；重点观察 `.观命 -> .定命 -> .推命/.改命` 是否按时间线推进且 action_guard 无残留。

## 2026-06-29 10:49 定命确认后继续推进修复

监测确认 `.观命 -> .定命 贪狼` 已跑通，且 `tianxing_set_star` action_guard 已闭合。但定命真实确认后，`auto_next_time` 又按“已有固定命星”的状态查询退避推到 6 小时后，时间线停在 `state_confirmed`，不会及时重算下一步推命。

根因：

- `_confirm_tianxing_timeline_from_observation()` 只更新 `tianxing_timeline_state=state_confirmed`。
- `apply_tianxing_passive()` 已经在确认前按固定命星分支写入长退避。
- 普通调度下一轮不会及时调用时间线完成旧 step / 重算新计划。

修复：

- 当真实回复确认时间线战略 step 后，`apply_tianxing_passive()` 将 `auto_next_time` 压到 60 秒内。
- `run_tianxing_scheduler()` 处理时间线返回 `completed/state_confirmed/downstream_released` 时，也使用 60 秒短退避，推动下一轮快速重算。

复测：

- `tests/test_tianxing.py -q`：`59 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`362 passed, 56 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

下一步：重启服务、重新开启 `3823558636 / 小耳朵图图` 天星，6 小时监测重新计时；重点观察定命后是否继续进入推命/等待深度闭关占用结束。

## 2026-06-29 10:59 已定命无推命长睡眠修复

重新监测发现 `3823558636 / 小耳朵图图` 已通过 `.天机盘` 校准为：

- 今日已定命星：`贪狼`
- 当前推命：无
- 当前改命：无
- 天机值：`3`

但 `auto_next_time` 被普通状态查询逻辑推到约 6 小时后，导致当前处于 farm window 时也不会进入 `.推命` / `.改命` 时间线。

根因：

- `apply_tianxing_passive()` 对“已有固定命星”的天机盘状态默认走 6 小时长退避。
- 该分支没有区分“已有固定命星且战略链路已完成”和“已有固定命星但没有推命/改命，仍需时间线规划”。
- `run_tianxing_scheduler()` 在最前面尊重未来 `auto_next_time`，因此重启/恢复后即使当前在 farm window，也会被历史长退避挡住。

修复：

- 新增时间线短唤醒判断：当 `timeline_enabled=true`、当前处于天星 farm window，且根据当前观察值构造出的时间线计划存在待执行步骤时，认为需要短唤醒。
- `apply_tianxing_passive()` 在“已定命”分支中应用该判断，把下一次自动调度压到 60 秒内。
- `run_tianxing_scheduler()` 对已经写入未来 `auto_next_time` 的旧状态也应用同一判断，允许 farm window 内的时间线修复穿透长退避。
- 仍不绕过安全锁：实际 `.推命` / `.改命` / `.天机盘` 等命令继续由时间线 step 串行发送，等待真实回包或超时校准后才推进下一步。

复测：

- `tests/test_tianxing.py -q`：`61 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`364 passed, 56 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

链路复盘：

- 0 点后先 `.观命 -> .定命`，定命一天一次，是后续推命/改命的前提。
- 推命不是随机发，而是根据主动 farm 窗口和被动业务时间线插入：普通闭关攒天机点、野外历练、探寻裂缝、斗法等都归到四类路线之一。
- 改命根据天机值、下一个消费窗口和风险来决定：目前重点是 `改命 探索` 后放行 `深入` / `探寻裂缝`。
- 未决策略：当天机值不足以改命时，野外 `深入` 不应硬上，建议改成 UI 可调降级为 `谨慎` 或 `均衡`；探寻裂缝没有谨慎档，建议等待/跳过，避免无保护消费。

下一步：重启服务、重新开启 `3823558636 / 小耳朵图图` 天星，重新开始 6 小时监测；如再出现天星 action_guard 残留、长退避误睡、推命/改命顺序异常，先关闭目标天星再回 lab 修复。

## 2026-06-29 11:10 炼制 Farm 策略复盘

用户提出用固定低成本炼制替代普通闭关攒天机：

- 每天 0 点后先完成 `.观命 -> .定命`。
- 再循环 `.推命 炼制 -> .炼制 玄铁剑`，例如 21 轮；单次成本约 10 灵石，速度和稳定性优于普通闭关。
- 后续探索消费段再按 `.推命 探索 -> .改命 探索 -> .野外历练 深入` 跑 13 组。
- 探寻裂缝段按 `.推命 探索 -> .改命 探索 -> .探寻裂缝` 跑 3 组。

评估结论：

- 方向成立，且比普通闭关 farm 少受深度闭关/CD/合气丹影响。
- 顺序必须修正为先观命/定命，再推命炼制；不能先推命再观定。
- 实现不能一次性预排 93 条命令，必须做确认式循环：推命确认、炼制结算确认并看到 `【推命命中】` 清掉当前推命后，才能进入下一轮。
- 当前代码中 `炼制` 仍是三期预留，只保留 route provider 概念，没有稳定的炼制命令状态机、真实文案解析、材料/灵石不足处理、吞回复校准和安全锁闭合；不能直接上线切换。

建议的 lab 待办：

- 新增天星炼制 farm 子状态：目标命中次数/目标天机、炼制物品、每日成本上限、单轮间隔、失败退避、查盘校准。
- 补真实文案解析：炼制成功、炼制失败、材料不足、灵石不足、未习得配方、推命命中/落空叠加文案。
- 接入时间线：每轮只在 `current_prediction` 为空时申请 `.推命 炼制`，推命确认后发送 `.炼制 玄铁剑`；炼制回包确认前不继续下一轮。
- 探索消费段保留现有 `探索` 时间线，但补策略开关：天机不足无法 `.改命 探索` 时，野外可配置降级 `谨慎/均衡/等待`，探寻裂缝默认等待/跳过。
- UI 增加炼制 farm 配置，并保留闭关 farm 开关；默认不实发，先 dry-run 审计。

运行备注：

- 11:10 实时检查发现 `3823558636 / 小耳朵图图` 当前仍按旧配置运行：`farm_route=闭关`、`retreat_farm_enabled=1`、窗口 `09:47` 起 240 分钟、目标天机 `42`。
- 当前 `.推命 闭关` 已确认，约 8 小时内不能切换 `.推命 炼制`，否则会触发异路推命冲突保护。
- `retreat_farm_allow_force_exit=0`、`retreat_farm_allow_heqi_dan=0`，深度闭关 running 且远端锁到约 17:55，因此旧链路不会强行打断深度闭关。

## 2026-06-29 11:35 炼制 Farm 上线前修正

用户纠正：推命不应被理解成绝对死锁。正确时间线是下游动作临近时插入推命，并在“推命确认 -> 下游动作触发 -> 真实结算确认”这一小段时间内排他，而不是在一个宽 farm window 开始时提前押路线。

结构修正：

- 移除普通天星 scheduler 在宽 farm window 内直接跑 timeline 的行为，避免再次提前数小时 `.推命 闭关`。
- 新增 `build_tianxing_consume_window(...)`，供下游动作按自身到期时间构造 `consume` 窗口；深度闭关 launch gate 不再使用 farm window。
- 保留后续待办：深度闭关结算前 5 分钟应接 phaseful summary 时间线，让总结/结算消费推命；本次先去掉错误的提前押路线。

炼制 Farm：

- 新增 `tianxing_craft_farm`：默认物品 `玄铁剑`，默认每日上限 `42` 轮，默认目标天机 `42`。
- 状态机按 `推命炼制确认 -> .炼制 玄铁剑 -> 炼制结算确认` 串行推进。
- 真实回包 `炼制结束` 且包含 `【推命命中】` 时，记录本轮命中、天机估算 +1、清掉当前推命；回包超时先 `.天机盘` 校准，不重复补发同一炉。
- 新增 `allow_prediction_override_enabled`：开启后，若读盘显示异路推命仍在生效，可由时间线尝试 `.推命 炼制`，以真实回包为准；时间不可解析时仍阻断，避免脏状态硬发。
- UI 已加入炼制攒点配置：开关、dry-run、物品、每日上限、间隔、回复超时、改押推命、提前准备秒。

默认建议：

- 小耳朵图图启用炼制 farm 时建议：`farm_route=炼制`、`craft_farm_enabled=true`、`craft_farm_dry_run_enabled=false`、`craft_farm_item=玄铁剑`、`craft_farm_daily_limit=42`、`target_tianji_daily=42`、`allow_prediction_override_enabled=true`。
- 普通闭关攒点关闭，避免与深度闭关链抢资源。

复测：

- `tests/test_tianxing.py -q`：`65 passed, 4 subtests passed`
- `tests/test_message_evidence.py tests/test_module_manifest.py tests/test_ui_dual_track.py tests/test_control_bool_coercion.py -q`：`83 passed, 52 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`368 passed, 58 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

## 2026-06-29 11:55 短消费窗口与负回包闭环

用户纠正：深度闭关若已知 8h 到期，天星推命应在到期前约 5 分钟插入，并只在“推命确认 -> 本次结算触发 -> 真实结算确认”这个短窗口内排他，不存在提前 8h 锁死路线的必要。

实测证据：

- 小耳朵图图尝试 `.推命 炼制` 时，游戏回包：`你已有一道关于 【闭关】 的推命尚未应验，还需等待 7小时33分钟。`
- 这证明当前状态不能直接改押到炼制；安全策略应等待闭关推命被消费/过期，不能发送 `.炼制 玄铁剑` 去制造落空。
- 原状态机已记录该回包，但 active timeline 仍停在 `ack_timeout`，缺少“负回包即终态”的闭环。

修正：

- `apply_tianxing_passive()` 遇到 `推命 cooldown/尚未应验` 时，若回包路线与当前 active predict 相同，标记 `confirmed_existing_prediction`，后续可正常 release 对应路线。
- 若回包路线与当前 active predict 不同，标记 `prediction_conflict`，清空 active step，按回包剩余时间设置 `blocked_until`；对应 craft/retreat farm 进入 `prediction_conflict`，不再短间隔重试。
- `build_tianxing_craft_farm_plan()` / `build_tianxing_retreat_farm_plan()` 对 `prediction_conflict` 和 `timeline_waiting` 的 future `next_time` 直接等待，不反复拉起时间线。
- runner 在 timeline 调用返回后会重新读取当前 timeline；若被动回包已先写入 `prediction_conflict`，不再用旧 farm snapshot 覆盖回 `timeline_waiting`。
- 深度闭关 gate 新增 running 阶段提前窗口：`next_deep_retreat_time - route_prepare_lead_sec` 后开始构造 `闭关/consume` 窗口；只做天星准备，不提前改写真实闭关到期时间。
- 深度闭关到期前窗口仍不绕过安全锁：`.推命` 经发送队列和 action guard，真实回包确认后才写 `released_routes`，下游命令再执行。

当前策略：

- 小耳朵图图已有 `闭关` 推命时，炼制 farm 暂停，等待闭关推命被深度闭关结算消费或自然过期。
- 后续没有异路推命时，炼制 farm 继续按确认式循环：`.推命 炼制` 确认 -> `.炼制 玄铁剑` -> 结算确认/查盘校准。

复测：

- `tests/test_tianxing.py tests/test_phaseful_summaries.py -q`：`132 passed, 4 subtests passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`372 passed, 58 subtests passed`
- `compileall model tests`、`node --check model/web/static/js/module_cards_ui.js`、`git diff --check`：通过。

## 2026-06-29 12:20 合气丹兑换与补回深度闭关链路

用户手动验证了一条关键事实：已有 `闭关` 推命不必等满 8 小时，只要触发一次普通 `.闭关修炼` 路线动作，就可以结算 `【推命命中】` 并拿到天机值。实测链路：

- `.强行出关` 后进入普通闭关调息。
- `.闭关修炼` 撞 `灵气尚未平复`。
- `.兑换 合气丹*10` 回包 `兑换成功！你消耗了 1500 点贡献，获得了【合气丹】x10`。
- `.服用 合气丹` 回包 `你服下一枚【合气丹】...可以继续闭关了`。
- `.闭关修炼` 即使本次闭关失败，仍可出现 `【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30`，并消费掉当前 `闭关` 推命。

修正：

- 普通闭关攒点状态新增 `cooldown_until`，服丹、兑换、捐献等待回复时不丢原普通闭关冷却。
- 新增真实文案解析：缺少合气丹、兑换合气丹成功、兑换贡献不足、宗门捐献灵石成功。
- 新增可配置链路：缺丹 -> `.兑换 合气丹*n`；贡献不足 -> `.宗门捐献 灵石*n` -> 回到兑换；兑换成功 -> `.服用 合气丹` -> `.闭关修炼`。
- 新增 UI 配置：缺丹自动兑换、兑换数量、贡献不足捐灵石、捐献灵石数量；默认仍全部关闭。
- `.兑换 合气丹*` 与 `.宗门捐献 灵石*` 已注册到 `tianxing_retreat_farm` family 和 action guard，回包会关闭同 family 会话，避免“失败后无法重试兑换”或“回复进不到天星链路”。
- 仍不绕过全局发送锁/安全锁：每一步都必须发送后等待真实回复，再由状态机推进下一步。

深度闭关交还原则：

- 强行出关只允许在显式配置开启时发生。
- 天星普通闭关链只做临时接管；深度闭关 gate 会避让 `sent_waiting_reply`、`calibrating`、缺丹兑换、贡献捐献、服丹以及普通闭关冷却阶段。
- 天星链路进入 ready 但仍有 `闭关` 推命待消费时，深度闭关继续短暂让路，保证下一拍先发 `.闭关修炼` 拿天机点；推命结算/查盘后进入 complete、或普通闭关冷却结束且无待消费推命时，才交还深度闭关模块按自己的 CD/phase 接手。

复测：

- `tests/test_tianxing.py -q`：`74 passed, 4 subtests passed`
- `tests/test_phaseful_summaries.py -q`：`65 passed`
- `tests/test_ui_dual_track.py -q`：`7 passed`
- 天星/深度闭关/安全锁/UI/真实回放/控制开关/启动恢复宽集：`379 passed, 58 subtests passed`
- `compileall model tests`、`git diff --check`：通过。
- `node --check model/web/static/js/module_cards_ui.js`：通过。

## 2026-06-29 12:24 服丹 ready 状态避免深闭抢跑

补充审计发现：主调度顺序是深度闭关先跑、天星宗后跑。若 `.服用 合气丹` 成功后，天星普通闭关 farm 进入 `ready`，但 `闭关` 推命仍未由 `.闭关修炼` 消费，深度闭关可能在下一轮先补回，导致“出来了但还没拿到天机点”。

修正：

- 深度闭关 gate 新增 `ready + current_prediction=闭关 + current_prediction_until>now` 的短阻断。
- 阻断只延后 `DEEP_RETREAT_TIANXING_RETRY_MIN_SEC` 加缓冲，给天星下一轮发送 `.闭关修炼`；拿到 `【推命命中】` 后解析会清空当前推命，深度闭关不再被这个条件拦住。
- 读取 `tianxing_observation` 时使用规范化函数，避免运行态字段格式漂移导致 gate 异常。

复测：

- `tests/test_phaseful_summaries.py::PhasefulSummaryTests::test_deep_retreat_waits_for_tianxing_ready_retreat_prediction`：通过。
- `tests/test_phaseful_summaries.py tests/test_tianxing.py -q`：`140 passed, 4 subtests passed`。

## 2026-06-29 12:35 主动攒点默认策略收窄

用户确认：主动攒点三类能力里，默认先只跑炼制；普通闭关攒点和斗法前置还未深入完善，必须保留为显式配置能力，不能因为开启天星宗就自动启用。

修正：

- 新增 `duel_route_enabled`，默认 `false`。默认态下斗法模块即使开启、天星时间线也开启，也不会主动插 `.推命 斗法` / `.改命 斗法`。
- 斗法前置只在 `duel_route_enabled=true` 时进入时间线：先推命/改命并等待真实确认，再放行 `.斗法 @目标`。
- 已知存在异路推命尚未应验时，斗法仍会被安全预检阻断，避免明确可见的逆命风险。
- UI 的天星宗设置里新增“斗法前置”开关；默认关闭。
- 普通闭关攒点维持已有默认：`retreat_farm_enabled=false`、`retreat_farm_dry_run_enabled=true`、`retreat_farm_allow_force_exit=false`、`retreat_farm_allow_heqi_dan=false`。

默认建议：

- 小耳朵图图当前只开炼制攒点：`farm_route=炼制`、`craft_farm_enabled=true`、`craft_farm_dry_run_enabled=false`。
- 普通闭关攒点和斗法前置保持关闭，等真实链路继续补文案和风控后再打开。

复测：

- `tests/test_duel.py tests/test_tianxing.py tests/test_ui_dual_track.py -q`：`97 passed, 4 subtests passed`。
- 天星/斗法/深度闭关/探索/安全锁/UI/真实回放/启动恢复宽集：`445 passed, 63 subtests passed`。
- `compileall model tests`、`git diff --check`：通过。
- `node --check model/web/static/js/module_cards_ui.js`：通过。

## 2026-06-29 12:48 炼制 release 复用缺陷

现场问题：

- 小耳朵图图第一轮 `.推命 炼制` 成功，第一把 `.炼制 玄铁剑` 出现 `【推命命中】`，天机值增加。
- 第一把结算后游戏已消费当前推命，`current_prediction` 被清空。
- 旧时间线仍保留 `released_routes.炼制`，原实现只按 release 年龄判断 1 小时内有效，导致第二把直接 `.炼制 玄铁剑`，结算为普通 `success`，没有天机值。

根因：

- `release_downstream` 被实现成时间窗口放行，但天星推命实际是“下一次行为”消费型资源。
- 下游动作完成并清空 `current_prediction` 后，release 必须失效；否则主动攒点会变成“一次推命，多次炼制”，后续炼制拿不到天机点。

修正：

- `release_downstream` 记录 `basis`：`prediction` 或 `change_fate`。
- `is_tianxing_route_released()` 不再只看 `released_at`，还要校验当前观测状态：
  - `basis=prediction`：必须仍有同路线 `current_prediction` 且未过期。
  - `basis=change_fate`：必须仍有同路线 `current_change` 且未过期。
  - 旧 release 无 basis 时，至少要有同路线推命或改命仍活跃。
- 新增复现测试：炼制推命命中后清空 `current_prediction`，旧 `released_routes.炼制` 立即失效；下一轮 craft farm 只能重新进入时间线，不能直接发送 `.炼制 玄铁剑`。
- 顺手补齐探寻裂缝 lead-window 接入，并修复野外历练脏 `next_wild_training_time` 在天星提前窗口判断前 `float()` 崩溃的问题。

复测：

- `tests/test_tianxing.py -q`：`75 passed, 4 subtests passed`。
- 天星/斗法/深度闭关/野外/探缝/安全锁/UI/真实回放/控制开关/启动恢复宽集：`448 passed, 63 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 12:56 炼制 preparing 中间态重复发送缺陷

现场问题：

- 修复 release 复用后，活体已按顺序执行 `.推命 炼制` -> 收到推命成功 -> release -> `.炼制 玄铁剑`。
- 但炼制先返回 `准备同时开炼 1 炉【玄铁剑】...` 中间文案，最终结算尚未到达前，脚本又发了第二次 `.炼制 玄铁剑`。
- watchdog 再次 fuse：`same command repeat: 3823558636:.炼制 玄铁剑 gap 41.0s`。

根因：

- `build_tianxing_craft_farm_plan()` 对 `sent_waiting_reply` / `crafting_waiting_final` 会正确返回 `waiting_reply`。
- `run_tianxing_craft_farm_scheduler()` 没有专门处理 `waiting_reply`，落入“无 command 的泛化等待”分支，把 pending phase 降级成普通 `waiting`。
- 下一轮 `waiting` 不在 pending 阻断集合中，于是同一炉最终结算前被误判为可再次发送。

修正：

- 调度器新增 `waiting_reply` / `waiting_calibration` 分支：
  - `waiting_reply` 保留原 `sent_waiting_reply` 或 `crafting_waiting_final` phase。
  - `waiting_calibration` 保留 `calibrating` phase。
  - 不发送任何命令，只记录等待审计并按原 `next_time` 等真实回复或查盘校准。
- 新增两条回归测试：
  - `sent_waiting_reply + next_time 未到` 不重发 `.炼制 玄铁剑`。
  - 收到 `准备同时开炼...` 后进入 `crafting_waiting_final`，在最终结算前不重发。

复测：

- `tests/test_tianxing.py -q`：`77 passed, 4 subtests passed`。
- 天星/斗法/深度闭关/野外/探缝/安全锁/UI/真实回放/控制开关/启动恢复宽集：`450 passed, 63 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 13:00 炼制成功但无天机点的校准保护

现场问题：

- 用户确认第二次炼制成功文案里没有天机点收益。
- 这说明 `.炼制 玄铁剑` 没有绑定到有效 `.推命 炼制`，不能把普通 `success` 当成天星攒点成功。

根因：

- 第一类缺陷是旧 release 复用，已经让“推命一次，多次炼制”成为可能。
- 第二类缺陷是炼制链路只要看到普通成功就回到 ready，缺少“推命仍活跃但结算没有天机点/逆命信息”的异常判断。
- 在脏运行态下，这会让脚本继续发下一把，既拿不到天机点，也会撞安全锁。

修正：

- `_update_craft_farm_from_parsed()` 对普通 `success` 增加状态交叉校验。
- 如果当前观测里 `current_prediction=炼制` 仍活跃，但结算没有推命命中/落空字段，则判定为可疑普通结算：
  - 不计为天星攒点成功。
  - 不进入下一轮炼制。
  - 进入 `calibrating`，等待 `TIANXING_CRAFT_FARM_CALIBRATION_DELAY_SEC` 后发送 `.天机盘` 校准。
- 回归测试覆盖：普通成功但推命仍活跃时，下一步必须是 `.天机盘`，不能继续 `.炼制 玄铁剑`。

复测：

- `tests/test_tianxing.py -q`：`78 passed, 4 subtests passed`。
- 天星/斗法/深度闭关/野外/探缝/安全锁/UI/真实回放/控制开关/启动恢复宽集：`451 passed, 63 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 13:08 自动查盘 pending 与并发调度缺陷

现场问题：

- 恢复全局并重置小耳朵图图天星后，脚本在 `13:02:29` 发送 `.天机盘`。
- `13:02:30` 另一轮天星调度又尝试 `.天机盘`，被安全锁拦截：`天机盘 本轮已发送 1/1 次，等待结果或人工处理`。
- 随后链路仍推进到 `.推命 炼制`，说明普通自动动作没有像时间线步骤一样等待查盘回包。

根因：

- `run_tianxing_timeline_scheduler()` 已有同身份锁和 `sent_waiting_ack`，但外层 `run_tianxing_scheduler()` 没有同身份锁。
- 普通自动动作（`.天机盘`、`.观命`、`.定命`、`.推命`、`.改命`、`.消劫`）发送后只更新 `auto_next_time`，没有记录“已发出并等待真实回包”的 pending 状态。
- 在调度 tick 接近、服务刚恢复或状态尚未保存完成时，下一轮会再次尝试发送，最后由安全锁兜底。

修正：

- 新增同身份 `run_tianxing_scheduler()` 外层锁，避免同身份天星自动调度并发进入。
- `tianxing_observation` 新增自动动作 pending 字段：
  - `auto_pending_action`
  - `auto_pending_command`
  - `auto_pending_msg_id`
  - `auto_pending_sent_at`
  - `auto_pending_due_at`
- 普通自动动作发送前先登记 pending；发送成功后写入消息 ID 和回包截止时间。
- pending 未到期时调度器直接等待，不再次调用 `send_game_command()`。
- pending 超时后清空 pending，并退避 `TIANXING_AUTO_SEND_FAIL_BACKOFF_SEC`，不继续推进下游。
- 收到对应真实回包后由 `apply_tianxing_passive()` 清理 pending。

复测：

- `tests/test_tianxing.py -q`：`81 passed, 4 subtests passed`。
- 天星/斗法/深度闭关/野外/探缝/安全锁/UI/真实回放/控制开关/启动恢复宽集：`454 passed, 63 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 13:16 新时间线重置炼制统计缺陷

现场问题：

- 小耳朵图图连续两轮验证均正常命中：
  - `.天机盘` 读到当前推命为炼制，天机值 `8`。
  - 第一轮 `.炼制 玄铁剑` 结算带 `【推命命中】`，天机值到 `9`。
  - 第二轮 `.推命 炼制 -> .炼制 玄铁剑` 结算带 `【推命命中】`，天机值到 `10`。
- 但创建下一轮 `tianxing_timeline_state` 时，`craft_farm` 统计被重置成 `idle/0`。

根因：

- `_build_tianxing_timeline_state_from_plan()` 每次从空时间线构建新 plan。
- 该函数只应该重置时间线步骤，不应该清掉主动 farm 的累计统计。
- 目标天机仍能靠 `tianxing_observation.tianji_value` 停住，但 UI 统计、每日次数和审计链会漂移。

修正：

- 新建时间线时继承旧 `retreat_farm` 和 `craft_farm` 状态。
- 新增回归测试：已有 `craft_farm.daily_count/hit_count/estimated_tianji` 时，新时间线发送 `.推命 炼制` 不得重置这些统计。

复测：

- `tests/test_tianxing.py -q`：`82 passed, 4 subtests passed`。
- 天星/斗法/深度闭关/野外/探缝/安全锁/UI/真实回放/控制开关/启动恢复宽集：`455 passed, 63 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 13:34 炼制 Farm 推命复核与裸结算计数缺陷

现场问题：

- 用户指出脚本炼制前没有看到对应 `.推命 炼制`，且炼制成功文案里没有天机点。
- 复查当前 live 状态时，小耳朵图图最近一轮真实结算为 `prediction_hit`，但时间线仍存在结构风险：
  - Farm 下游放行可由面板/旧状态里的 `current_prediction=炼制` 推导出来。
  - 普通炼制 `success/failure` 即使没有 `【推命命中】/【推命落空】`，也会占用 `daily_count/success_count`。

根因：

- `build_tianxing_timeline_plan()` 对同路线活跃推命的信任条件过宽，无法区分“本轮真实推命回复”与“面板/旧状态残留”。
- `_update_craft_farm_from_parsed()` 的计数条件只看是否处于等待回包阶段，没有要求结算文案包含推命结算标记。

修正：

- 新增推命新鲜度判断：只有最近真实 `推命 success/cooldown`，且发生在上一轮炼制结算之后，才允许 Farm 直接放行。
- 如果只从面板/旧状态读到同路线 `current_prediction`，时间线改为先发送 `.推命 <路线>` 复核；收到同路线冷却也算确认，再放行下游。
- 普通炼制 `success/failure` 不再增加 `daily_count/success_count/hit_count`，并进入 `calibrating`，先 `.天机盘` 校准，不能直接下一轮炼制。

复测：

- `tests/test_tianxing.py -q`：`84 passed, 4 subtests passed`。
- 全量测试：`1854 passed, 351 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 13:55 野外历练改命策略缺陷

现场问题：

- 用户确认期望：`改命 探索` 已经待发时，野外历练应走 `深入`；没有改命时必须走 `谨慎`。
- 旧逻辑中野外历练默认策略是 `深入`，且天星预检只负责放行路线，不会把“是否有改命”映射到野外策略。

根因：

- `wild_training_strategy` 默认值与 normalize fallback 都是 `深入`。
- 野外发送前只读取 UI 配置，没有根据 `tianxing_observation.current_change=探索` 做本次有效策略覆盖。

修正：

- 默认野外策略改为 `谨慎`。
- 天星开启时，野外发送前临时计算本次有效策略：
  - `current_change=探索` 且未过期：发送 `.野外历练 深入`。
  - 否则：发送 `.野外历练 谨慎`，即使 UI 旧配置仍是深入也不裸深入。
- UI 卡片默认展示 fallback 从 `深入` 改为 `谨慎`。

复测：

- `tests/test_wild_training.py tests/test_tianxing.py -q`：`108 passed, 4 subtests passed`。
- 全量测试：`1855 passed, 351 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 14:08 校准后重算长退避缺陷

现场问题：

- 重启后小耳朵图图天星一轮 `.推命 炼制` 处于发送队列等待，最终进入 `ack_timeout`。
- 时间线按设计没有重复推命，而是等待 `.天机盘` 校准。
- 校准成功后状态显示“需重算时间线，不放行下游”，但实际停在 `blocked_replan`，反复等待，没有立刻重算。

根因：

- `terminal_after_confirm` 的校准完成分支把 `blocked_until` 设置为 `now + status_backoff`。
- 默认 `status_backoff` 为 6 小时，导致“重算”实际变成长时间休眠。

修正：

- 校准确认后仍进入 `blocked_replan`，继续禁止下游放行。
- 但 `blocked_until` 改为当前时间，下一次调度即可重算时间线并重新发 `.推命 <路线>` 复核。

复测：

- `tests/test_tianxing.py tests/test_wild_training.py -q`：`108 passed, 4 subtests passed`。
- 全量测试：`1855 passed, 351 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 14:22 野外无改命出口修复

现场问题：

- 用户再次确认野外原则：`改命 探索` 已确认/待发时，`.野外历练` 才走 `深入`；没有探索改命时应走 `谨慎`。
- 复查发现 13:55 的策略修复只保证了“发送时选择谨慎”，但天星前置预检仍可能一直要求时间线 release。
- 当天机不足、自动改命关闭、dry-run 或没有可执行时间线步骤时，野外到期会反复等待 release，无法进入谨慎出口。

根因：

- 野外模块把“路线放行”和“策略降级”混在一起：没有探索改命时策略会降级，但 preflight 仍认为天星时间线未放行。
- 天星时间线无步骤时返回 `idle/completed/dry_run`，这应表示没有可用改命可消费，而不是让野外无限等待。

修正：

- 野外到期后若已尝试天星探索消费时间线，且没有异路推命/异路改命冲突、没有待确认战略命令、也没有活跃 `改命 探索`，则允许本轮按 `谨慎` 发送。
- 若 `.改命 探索` 已发送并处于 `sending/sent_waiting_ack/ack_timeout`，仍继续等待，不抢跑。
- 若后续 preflight 读到探索路线已 release，则立即放行；发送策略仍由 `current_change=探索` 决定，避免“只有推命无改命”时裸深入。

复测：

- `tests/test_wild_training.py tests/test_tianxing.py -q`：`109 passed, 4 subtests passed`。

## 2026-06-29 14:36 炼制 Farm 抢占野外探索窗口风险

现场问题：

- 复盘调度顺序发现普通调度中 `run_tianxing_scheduler` 先于 `run_wild_training_scheduler`。
- 若 15:11 野外提前准备窗口到来时，炼制 farm 先发送 `.推命 炼制`，野外随后会因活跃异路推命被阻断，无法及时 `.改命 探索`。
- 这不是安全锁问题，而是主动 farm 与下游消费窗口之间缺少让路规则。

根因：

- 炼制 farm 只看自身 farm 窗口、目标天机和路线 release，没有读取已知探索消费窗口。
- 野外/探缝虽然会在自己的调度中构造 `build_tianxing_consume_window("探索")`，但调度顺序上它们拿不到优先权。

修正：

- `build_tianxing_craft_farm_plan()` 新增探索消费窗口让路：
  - 野外历练或探寻裂缝已进入 `route_prepare_lead_sec` 提前窗口时，炼制 farm 返回 `waiting_consume_window`，不发送 `.推命 炼制` 或 `.炼制 玄铁剑`。
  - 野外/探缝已有 pending 回复时，炼制 farm 继续让路到 pending 超时或短缓冲后。
  - 让路只暂停主动炼制 farm，不改变野外/探缝自己的安全锁和推命/改命确认流程。

复测：

- `tests/test_wild_training.py tests/test_tianxing.py -q`：`110 passed, 4 subtests passed`。
- 全量测试：`1857 passed, 351 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 15:18 探索改命待发阻断炼制缺陷

现场问题：

- 小耳朵图图 15:16 已按预期在 `改命 探索` 后发送 `.野外历练 深入`，并完成结算。
- 结算后 `current_change=探索` 仍处于待发/有效状态，炼制 farm 出现 `已有 探索 改命待发，等待时间线重算。`
- 若不修正，探索改命可能把后续炼制 farm 卡到改命自然过期，时间可能长达 24 小时。

根因：

- `build_tianxing_route_preflight_plan()` 把“异路推命”和“异路改命”都当成路线冲突。
- 推命会带来逆命风险，必须阻断异路动作；改命只是对应路线失败兜底，不应该阻断其他路线动作。

修正：

- 路线 preflight 保留异路推命阻断，移除异路改命阻断。
- 已有其他路线改命时，时间线只是不覆盖该改命；炼制/闭关/斗法等路线仍可按自己的推命确认流程推进。
- 新增回归测试：`current_change=探索` 有效时，炼制 farm 仍进入 `timeline_required`，不会被 `timeline_waiting_other_change` 卡住。

复测：

- `tests/test_wild_training.py tests/test_tianxing.py -q`：`111 passed, 4 subtests passed`。
- 全量测试：`1858 passed, 351 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。

## 2026-06-29 15:23 改命待发复用规则确认

用户补充：

- `改命 探索` 待发时，改命 CD 仍在。
- 到下一次野外历练或探寻裂缝时，不应再次 `.改命 探索`，应直接消费现有探索改命。
- 推命链路不受该待发改命影响；仍按自身路线推命确认流程推进。

实现约束：

- 时间线计划在 `current_change=探索` 且未过期时，只生成 `release_downstream 探索`，不再生成 `change_fate 探索`。
- 已有探索改命不阻断炼制 farm 的 `.推命 炼制`；异路推命仍保留逆命保护。
- 新增回归测试覆盖“已有探索改命 + 野外消费窗口”只 release、不重复改命。

复测：

- `tests/test_tianxing.py tests/test_wild_training.py tests/test_explore_rift.py -q`：`140 passed, 9 subtests passed`。
- 全量测试：`1859 passed, 351 subtests passed`。
- `node --check model/web/static/js/module_cards_ui.js`、`compileall model tests`、`git diff --check`：通过。
