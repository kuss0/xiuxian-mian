# 世界 Boss MiniApp 事件关闭修复审计（2026-07-28）

## 结论

7 月 17 日的 4/4 结算记录不能继续代表当前协议健康。7 月 27 日两轮生产运行均出现“入场 4、结算 0、失败 4”，四个身份最终均收到 `boss_event_closed`。

本轮只修青元子 `qyz_*` 的时间线收口和证据归并，不接入上游整套状态机，不启用 WebSocket，不重放失败的 `/hit` 或 `/finish`。

## 生产证据

- 日志时间：2026-07-27 10:54-10:57、13:40-13:42。
- 脱敏 capture：`data/messages/miniapp-captures/world_boss-2026-07-27.jsonl`。
- 四个身份均成功入场并取得 16 个窗口的 challenge。
- 单身份约完成 12-13 次正伤害请求后，下一次 `/hit` 返回 HTTP 409 `boss_event_closed`。
- challenge 回包包含 `expiresIn`，旧执行器却只按最后窗口和 `maxDurationMs` 决定收口，导致继续点击服务端寿命以后的尾部窗口。
- 首次命中后，服务端常返回 `attemptConsumed=false`，但同一回包仍有正 `damageYi`、`perfect=true` 和降低后的 boss HP。该字段表示入场/行动额度是否再次消耗，不能覆盖真实伤害证据。

## 修复边界

1. 将 `expiresIn` 转为动态服务端寿命，裁掉无法在安全结算截止线前完成的尾部窗口。
2. 给 `/finish` 保留固定网络余量，不在服务端过期点上压线提交。
3. 正 `damageYi` 或 `perfect=true` 优先作为有效贡献证据；`attemptConsumed=false` 不再清零真实伤害。
4. 四个账号继续并行战斗，但共享一个线程安全停止标记。任一身份确认 `boss_event_closed` 后，其余身份不再发送新的 `/hit`。
5. 关闭后每个身份最多执行一次只读 `/state` 校准；禁止自动重放 `/hit` 和 `/finish`。
6. `/state` 有权威结算时记为 `settled`；只有本地正贡献时记为 `event_closed_partial`，日志单列“部分”，不再伪报为零贡献失败。
7. 新增脱敏业务字段 `duration_ms`、`max_duration_ms`、`expires_in_ms`，供下一轮生产验收，不保存 token 或 initData。

## 上游对照

wxjerry `91f3626c` 新增 WebSocket 实时状态和重连，可用于未来独立 Lab。当前本地问题已有 HTTP 协议内的窄修路径，且生产尚无 WebSocket 依赖与票据 capture，因此本轮不整块移植，不增加生产依赖。

## 验收门槛

- focused tests：`tests/test_world_boss.py`、`tests/test_world_boss_miniapp.py`、`tests/test_world_boss_miniapp_runtime.py`。
- 全量 pytest、`py_compile`、`git diff --check` 通过。
- 上线后服务、watchdog、health observer 正常，pending 为空。
- 下一次真实世界 Boss 必须看到：尾部窗口按 `expiresIn` 裁切；4 个身份不再集中报 `boss_event_closed`；合并日志保留命中、完美、伤害与质量分。
- 未完成下一次真实事件前，本项仅为“代码上线待生产验收”，不得重新标为完全销号。

## 2026-07-29 尾窗口跟进

7 月 28 日 13:40 的真实事件 4/4 入场。吧唧配置少出手 2 个窗口后完整结算；WA、Lsfnqy、Wise 均完成 14 次服务端接受、13 次完美且产生真实伤害，但仍按 16 窗口计划等待，最终在全服提前击杀后记为 `event_closed_partial`。

本轮将结算余量与身份低调策略分层：

1. 所有身份固定预留尾部 2 个窗口，优先提交 `/finish`。
2. UI 的身份配置改为“额外少出手”，在固定预留之外叠加。
3. 吧唧与 WA 当前均额外少出手 2；其他账号只使用固定结算预留。
4. 运行结果分别记录 `finish_reserve_window_count` 与 `identity_extra_window_skip_count`，日志不再把安全预留误报为身份低调策略。
5. WA 保持候选优先级 0；4 个登录账号仍可并行，单账号内部请求继续串行。

离线验收：focused `179 passed`；全量 `3395 passed, 535 subtests passed`。生产已部署，下一场真实事件仍是最终销号门槛。

## 2026-07-30 少出手身份结算修复

7 月 29 日生产 capture 暴露了“额外少出手”与服务端最短结算时长之间的独立缺口：

- WA (`8659059191`) 与吧唧登录账号 (`301299112`) 均按配置完成 12 次有效命中，命中请求全部成功。
- 两个身份在约 `67.5s` 提交 `/finish`，服务端均明确返回 HTTP 400 `boss_duration_too_short`。
- 另外两个只使用固定结算预留的身份完成 14 次命中，并在约 `78.8s` 成功结算。
- 根因是裁掉两个尾部窗口后，执行器同时用最后一个“实际出手窗口”提前了结算时刻，忽略 challenge 的 `minDurationMs`。

修复后，额外少出手只减少 `/hit` 数量；存活身份仍保持时间线至服务端 `minDurationMs` 后再提交 `/finish`，不会补打被裁掉的窗口。死亡结算仍允许早于普通最短时长，避免改变原有死亡终态。proof 的显式 `durationMs` 同样不能低于可行的服务端最短时长，并受 `maxDurationMs`/`expiresIn` 上限约束。

离线验收：世界 Boss focused `107 passed`；全量 `3469 passed, 536 subtests passed`；`py_compile` 与 `git diff --check` 通过。下一场真实事件需确认两个额外少出手身份均保留 12 次目标命中且 `/finish` 不再返回 `boss_duration_too_short`，通过后再销号。

## 2026-07-30 生产验收与结算竞态收口

13:40 自然事件完成最终生产验收：

- WA (`8659059191`) 与吧唧 (`301299112`) 均按额外少出手配置完成 `12/16` 命中、`12` 次完美，`/finish` HTTP 200 并归类为 `settled`。
- 本场没有再次出现 `boss_duration_too_short`，证明额外少出手已只减少 `/hit`，没有缩短服务端最短结算时长。
- Lsfnqy 与 Wise 在全服提前结束时分别保留 `14/13` 次有效命中，归类为 `event_closed_partial`；没有失败身份或重复入场/结算。

本场还暴露了独立的异步竞态：结算广播处理器在等待日志群发送期间持有旧的 `running` 快照，MiniApp 任务完成并写入结果后，处理器会把旧快照整体写回，清空结果并触发下一轮“任务中断”误报。修复后 `_close_event()` 先持久化事件关闭边界，再发送日志，等待结束后只向最新同场状态合并关闭字段，不再覆盖 MiniApp 的 status/progress/results；若等待期间已经切换到新事件，也不会回写旧场状态。

新增回归模拟日志等待期间 MiniApp 完成写入，确认最终仍为事件关闭、`partial` 状态和完整结果；同时修正实时唤醒用例中会被后续随机 `1.2s` 等待误撞的脆弱断言。验证：相关 `214 passed, 3 subtests passed`，实时唤醒用例重复 10 次通过，全量 `3486 passed, 536 subtests passed`，compileall 与 `git diff --check` 通过。

部署前备份：`data/state/chaogu_state.db.pre-world-boss-close-race-20260730-1355`。本场运行账本已按脱敏 capture 校准为 WA/吧唧 `settled`、另外两号 `event_closed_partial`、事件 `功成`。14:00 主服务恢复后 24 身份加载成功，pending 为空，watchdog 正常，`NRestarts=0`。本验收项销号。
