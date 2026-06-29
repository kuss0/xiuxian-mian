# 天星宗 lab 开发计划

日期：2026-06-29

## 边界

- 只在 `/opt/xiuxian-main` lab 工作区设计、编码和测试。
- 不触碰 `/opt/xiuxian`、`/opt/xiuxian-c`。
- 不 push、不上线、不重启生产。
- 所有实发仍必须走 `send_game_command()`、全局发送锁、`action_guard`、watchdog 与审计日志。
- 安全锁是执行层硬闸，不是时间线规划器；不能绕过，也不能依赖它决定天星路线顺序。

## 核心问题

天星宗不是独立模块，而是横切四条原有路线：

- `闭关`
- `斗法`
- `探索`：`野外历练`、`探寻裂缝`
- `炼制`

`推命` 是 8 小时路线承诺，`改命` 是 24 小时路线兜底。若让各模块临发前各自补 `推命/改命`，会造成结构性竞态：

- 模块 A 可能准备 `改命 探索`
- 模块 B 或天星 scheduler 可能准备 `推命 闭关`
- 全局发送锁只能保证串行发送，不能保证语义顺序正确
- 结果可能造成错误路线动作触发 `推命落空 -> 逆命劫 +1`

因此必须先做统一时间线，再让各模块消费授权。

## 确认推进原则

天星链路不能“发了就算”。每个战略动作至少分三层确认：

1. `queued`
   - 命令进入计划或等待发送。
   - 不能推进下一步。

2. `sent_waiting_ack`
   - `send_game_command()` 返回 `msg_id`。
   - 只能说明 Telegram 命令已发出，不代表游戏状态已生效。
   - 不能放行下游动作。

3. `state_confirmed`
   - 被动解析真实回复，或主动 `.天机盘` 校准确认状态。
   - 只有此状态允许推进下一步。

战略动作确认口径：

- `定命`：回复出现 `你将今日命轨定在【X】`，或 `.天机盘` 显示 `今日已定命星: X`。
- `推命`：回复出现 `为【路线】推下一段命数`，或 `.天机盘` 显示 `当前推命: 路线` 且剩余时间有效。
- `改命`：回复出现 `为【路线】预留了一次改命回天`，或 `.天机盘` 显示 `当前改命: 路线` 且剩余时间有效。
- `消劫`：回复出现 `成功化去 1 层逆命劫`，或 `.天机盘` 显示逆命劫下降。

若命令已发送但未确认：

- 不继续发送下游路线动作。
- 先等待真实回复。
- 超时后低频 `.天机盘` 校准。
- 校准仍不确认，则计划进入 `blocked_replan`，记录原因并等待下轮规划。

## 目标状态机

天星时间线计划使用单身份单计划模型：

```text
idle
  -> planning
  -> waiting_send
  -> sent_waiting_ack
  -> state_confirmed
  -> downstream_released
  -> completed
```

异常分支：

```text
waiting_send -> send_blocked
sent_waiting_ack -> ack_timeout
ack_timeout -> calibrating
calibrating -> state_confirmed | blocked_replan
planning -> prediction_conflict
any -> paused_by_safety
```

推进规则：

- 同一身份同一时间只能有一个天星战略计划。
- `推命/改命/定命/消劫` 不允许同 tick 连发。
- 战略动作确认前，不允许释放下游动作。
- 下游动作被释放后仍走原模块自身安全锁与回复确认。
- 任何状态字段脏值、时间不可解析、宗门身份不匹配，都 fail closed。

## 数据结构草案

继续保留现有：

- `tianxing_observation`
- `tianxing_auto_config`

新增 lab-only 运行态建议：

```python
tianxing_timeline_state = {
    "plan_id": "",
    "phase": "idle",
    "route": "",
    "reason": "",
    "created_at": 0,
    "updated_at": 0,
    "deadline_at": 0,
    "active_step": {},
    "steps": [],
    "released_routes": {},
    "blocked_until": 0,
    "last_error": "",
    "audit": [],
}
```

`active_step` 建议字段：

```python
{
    "action": "set_star|predict|change_fate|clear_calamity|release_downstream",
    "arg": "太阴|闭关|探索|斗法|炼制",
    "command": ".推命 闭关",
    "expected_family": "tianxing_predict",
    "expected_state": {"current_prediction": "闭关"},
    "send_msg_id": 0,
    "sent_at": 0,
    "ack_due_at": 0,
    "calibration_due_at": 0,
}
```

## 配置草案

UI 只暴露安全可理解的配置，不暴露内部状态机字段。

建议新增：

- `timeline_enabled`：时间线规划总开关，默认关闭。
- `timeline_dry_run_enabled`：只记录计划不发战略动作，默认开启。
- `farm_route`：默认 `闭关`。
- `farm_window_enabled`：是否启用每日固定 Farm 窗口，默认开启但 dry-run。
- `farm_window_start`：每日 Farm 窗口开始时间，例如 `02:00`。
- `farm_window_duration_min`：Farm 窗口长度。
- `target_tianji_daily`：目标天机值，默认 `42`。
- `min_tianji_for_change`：低于阈值不自动改命。
- `change_route_priority`：默认 `探索,斗法,闭关,炼制`。
- `ack_timeout_sec`：战略命令等待真实回复时间。
- `calibration_backoff_sec`：超时后查盘校准间隔。
- `max_replans_per_day`：防止反复重算。

## 路线矩阵

### 闭关

一期只接现有 `深度闭关` 链路。

用途：

- 作为默认 Farm 路线，配合 `.推命 闭关` 获取天机值。
- 只在时间线规划出的 Farm 窗口内考虑推命。

约束：

- 不抢原有深度闭关 phaseful 结算。
- 不在闭关总结等待期插入天星战略动作。
- 推命闭关确认前，不触发新的闭关动作。

### 探索

一期已接入：

- `野外历练`
- `探寻裂缝`

用途：

- 主要消费 `改命 探索`。
- 只有时间线判定未来 8 小时探索为主路线时，才考虑 `.推命 探索`。

约束：

- 有异路推命未应验时阻断探索动作。
- 改命探索发送后必须等待确认，再放行野外/裂缝。

### 斗法

二期接入。

用途：

- 只服务明确配置目标、次数和窗口的主动斗法。
- 更适合 `改命 斗法`，推命斗法必须由斗法窗口显式触发。

约束：

- 无目标、无次数、无时间窗时不做天星前置。
- 不因天星自动调度主动制造斗法。

### 炼制

三期预留。

当前未确认稳定炼制模块与命令链路，先只保留 route provider 接口，不接实发。

## 开发阶段

### P0 文档与审计口径

交付物：

- 本计划文档。
- backlog 更新。
- 明确 `确认发送 != 状态确认`。

验收：

- 文档说明安全锁不绕过。
- 文档说明天星横切四路线。
- 文档说明状态确认后才推进。

### P1 纯规划器

文件：

- `model/features/tianxing.py`
- `tests/test_tianxing.py`

内容：

- 完善 `build_tianxing_timeline_plan(...)`。
- 输入 route windows，输出计划步骤。
- 不发送任何命令。

验收：

- 能识别闭关 Farm 窗口。
- 能识别探索消费窗口。
- 能阻断异路推命冲突。
- 能输出 `predict/change_fate/release_downstream` 顺序。

### P2 lab-only 执行状态机

文件：

- `model/features/tianxing.py`
- `model/state.py`
- `model/persistence.py`
- `tests/test_tianxing.py`

内容：

- 新增 `tianxing_timeline_state`。
- 实现 `run_tianxing_timeline_scheduler(now)`，默认 dry-run。
- 实现发送后等待真实确认，不确认不推进。

验收：

- dry-run 只记录计划。
- 安全锁拦截后进入 `send_blocked`。
- 收到真实文案后从 `sent_waiting_ack` 进入 `state_confirmed`。
- 超时后进入 `.天机盘` 校准，不直接放行。

### P3 探索链确认式接入

文件：

- `model/features/explore_rift.py`
- `model/features/wild_training.py`
- `tests/test_explore_rift.py`
- `tests/test_wild_training.py`

内容：

- 模块前置只向时间线层申请 `探索` 放行。
- 若时间线返回 `hold`，模块顺延短等待。
- 若时间线返回 `released`，模块再走原发送逻辑。

验收：

- `改命 探索` 已发送但未确认时，不发野外/裂缝。
- `.天机盘` 确认改命后，才放行。
- 异路推命仍阻断探索。

### P4 闭关 Farm 窗口

文件：

- `model/features/deep_retreat.py`
- `model/features/tianxing.py`
- `tests/test_phaseful_summaries.py`
- `tests/test_tianxing.py`

内容：

- 读取深度闭关可用状态，形成 `闭关` route window。
- 在每日固定 Farm 窗口内规划 `.推命 闭关`。
- 只在推命确认后释放闭关动作。

验收：

- 不打断闭关总结等待。
- 不与深度闭关安全锁冲突。
- 推命闭关未确认时不启动闭关。

### P5 斗法与炼制预留

斗法：

- 只接入有明确目标和窗口的主动斗法。
- 默认不自动开启。

炼制：

- 先补真实文案和命令链路。
- 只做 route provider，不实发。

### P6 UI

文件：

- `model/web/static/js/module_cards_ui.js`
- 相关 API 文件
- UI 测试

要求：

- 设置继续使用弹窗。
- 展示当前计划状态、阶段、等待确认的命令、阻断原因。
- 展示当前路线窗口：闭关 Farm、探索消费、斗法窗口、炼制预留。
- 所有战略实发开关默认关闭或 dry-run。

### P7 上线前审计

必须通过：

- `tests/test_tianxing.py`
- `tests/test_explore_rift.py`
- `tests/test_wild_training.py`
- `tests/test_phaseful_summaries.py`
- `tests/test_safety_watchdog.py`
- `tests/test_module_manifest.py`
- `tests/test_real_message_replay.py`
- `git diff --check`

上线前必须人工确认：

- 不绕过安全锁。
- 不在同 tick 连发战略动作和下游动作。
- 发送确认后仍等待状态确认。
- 生产默认 dry-run 或明确人工开启。
- 监控日志能解释每次 hold/release/replan。

## 当前优先顺序

1. 完成本计划审阅。已完成。
2. P1：把 `build_tianxing_timeline_plan(...)` 扩成真正步骤计划。已完成，输出 `predict / change_fate / release_downstream` 步骤。
3. P2：做 lab-only 执行状态机，默认 dry-run。已完成，新增 `run_tianxing_timeline_scheduler(...)` 与 `tianxing_timeline_state`。
4. P3：把探索链从“直接执行天星预检”改成“申请时间线放行”。已完成，`探寻裂缝` 和 `野外历练` 不再直接发 `.定命/.改命`，只申请 timeline 并等待 `release_downstream`。
5. P4：接闭关 Farm 窗口。已完成 lab 接入，只在深度闭关即将发起且处于 Farm 窗口时申请 timeline；等待/总结阶段不插天星动作。
6. P6：UI 展示与可调配置。已完成 lab UI 快照和天星设置弹窗：可配置时间线、dry-run、Farm 窗口、确认超时/校准退避，并展示 phase、当前步骤、已放行路线和最近 audit。
7. 通过审计后再讨论是否合入生产。

## 2026-06-29 lab 进展

- `tianxing_timeline_state` 已加入身份运行态与持久化 JSON 列。
- `run_tianxing_timeline_scheduler(...)` 默认不挂生产调度；`timeline_enabled=False`、`timeline_dry_run_enabled=True` 保持安全默认。
- dry-run 只记录计划，不发送。
- 非 dry-run 也不绕过安全锁：所有命令仍经 `send_game_command`、全局发送锁、action guard 与 watchdog。
- `sent_waiting_ack` 必须被真实天星文案或 `.天机盘` 状态确认，才进入 `state_confirmed`。
- `release_downstream` 是独立内部步骤，只有前置战略动作确认后才会写入 `released_routes`。
- 发送失败进入 `send_blocked`，不推进。
- 回复超时进入 `ack_timeout`，到期只安排 `.天机盘` 校准；校准完成后转 `blocked_replan`，不沿用原计划放行。
- 关闭/重新开启天星模块会清空旧 `tianxing_timeline_state`，避免旧 release 标记残留。
- 当前验证：`.venv/bin/python -m pytest tests/test_tianxing.py -q` 33 passed / 4 subtests passed；`.venv/bin/python -m pytest tests/test_tianxing.py tests/test_explore_rift.py tests/test_wild_training.py tests/test_control_bool_coercion.py -q` 100 passed / 12 subtests passed。

## 2026-06-29 P3 进展

- `build_tianxing_route_preflight_plan(...)` 改为 timeline gate：未开启 timeline 时不插战略命令；已开启 timeline 时，只有 `timeline_released` 才放行下游。
- 异路推命冲突仍在 route gate 层 fail closed，避免主动触发逆命劫。
- `探寻裂缝` 和 `野外历练` 遇到 `timeline_required` 时调用 `run_tianxing_timeline_scheduler(...)`，提交 `探索/consume` 窗口，然后按原模块重试节奏等待；不再调用 `execute_tianxing_manual_action(...)` 直接发 `.定命/.改命`。
- 已有匹配 `current_change/current_prediction` 不再被下游直接视为可发送；仍需 timeline 写入 `released_routes`，保证“状态确认 -> 内部 release -> 下游发送”的顺序。
- 当前验证：`.venv/bin/python -m pytest tests/test_tianxing.py tests/test_explore_rift.py tests/test_wild_training.py -q` 80 passed / 7 subtests passed；`.venv/bin/python -m pytest tests/test_tianxing.py tests/test_explore_rift.py tests/test_wild_training.py tests/test_control_bool_coercion.py tests/test_persistence_runtime_flags.py -q` 117 passed / 12 subtests passed；`compileall` 与 `git diff --check` 通过。

## 2026-06-29 P4 进展

- 新增 `build_tianxing_farm_window(...)`，按 UI 配置生成每日固定 Farm 窗口，默认路线为 `闭关`。
- `run_deep_retreat_scheduler(...)` 在真正准备发起深度闭关前调用 `_run_deep_retreat_tianxing_gate(...)`；只有 `timeline_released` 才放行闭关命令。
- 深度闭关的等待总结、被动结算、summary trigger 不受天星 Farm 影响；窗口外按原链路推进。
- 若已有异路推命未应验，闭关 Farm 也 fail closed 并顺延，避免制造逆命劫。
- 当前验证：`.venv/bin/python -m pytest tests/test_phaseful_summaries.py tests/test_tianxing.py -q` 93 passed / 4 subtests passed。

## 2026-06-29 P6 进展

- `get_identity_ui_snapshot(...)` 新增 `tianxing.timeline` 只读快照，展示 `phase/route/reason/active_step/steps/released_routes/audit/last_error`。
- 天星宗设置弹窗新增 `时间线规划`、`闭关 Farm 窗口`、`确认与重算`、`时间线观测` 四块配置/展示。
- UI 可调项包括 `timeline_enabled`、`timeline_dry_run_enabled`、`farm_route`、`farm_window_start`、`farm_window_duration_min`、`target_tianji_daily`、`change_route_priority`、`ack_timeout_sec`、`calibration_backoff_sec`、`max_replans_per_day`。
- 当前验证：`node --check model/web/static/js/module_cards_ui.js` 通过；`.venv/bin/python -m pytest tests/test_tianxing.py tests/test_ui_dual_track.py -q` 42 passed / 4 subtests passed。
