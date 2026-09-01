# 世界 Boss 战斗时钟锚定修复与生产验收（2026-08-30 / 08-31）

## 结论

`production_handoff_20260828.md` §8 的 P0「世界 Boss 全程零命中」已销号。

根因不是交接文档记录的那一条。`ba1760fb` 修的是动态窗口逐次预取，方向正确但没有触及真正的缺陷：
`timeline_origin`（本地战斗时钟原点）在 `reveal_world_boss_windows()` **之后**才计算，于是 reveal 阶段
消耗的全部墙钟时间被从本地时间线上抹掉。执行器据此认为自己还在战斗早期，
`skip_expired_window` 因而永远判定不出过期窗口，第一次 `charge-start` 就撞上服务端的
`boss_window_expired`。

修复把锚定点前移到服务端战斗时钟已知的那一刻（begin 同步之后、reveal 之前）。

## 生产证据

脱敏 capture：`data/messages/miniapp-captures/world_boss-2026-08-{29,30,31}.jsonl`，身份 `8613500668`。

| 指标 | 8/29（修复前） | 8/30（修复后） | 8/31（修复后） |
| --- | --- | --- | --- |
| 请求总数 | 12 | 131 | 133 |
| HTTP 200 | 10 | 113 | 115 |
| 失败 | 1 × 409 `boss_window_expired` | 0 | 0 |
| `window` 步骤 | 2 | 72 | 74 |
| `hit` | 0 | 16 | 16 |
| `finish` | 无 | 1 | 1 |

8/29 的 409 精确落在 `/api/miniapp/xianxia-world-boss/charge-start`，`error` 字段即
`boss_window_expired`，与上述根因逐字对应。该场没有任何 `hit`，也没有 `finish`。

修复后两场结算业务记录一致：

- `planned_window_count` / `completed_window_count` / `accepted_hit_count` = `16 / 16 / 16`
- `accepted_perfect_count` = 16，`quality_multiplier` = 1.3，`grade` = 甲等，`score` = 100
- `window_skip_count` = 0，`rejected_window_count` = 0，`full_window_run` = true
- `realtime_damage_applied` = true；伤害 8/30 `683,936,818` 亿、8/31 `632,668,124` 亿
- `dead` = false，`player_hp` = 100，`actions_used` = 0（未额外消耗行动额度）

8/31 是与 8/30 相互独立的第二次自然事件复现，不是同一场的重复观测。

## 修复边界

`model/features/world_boss_miniapp.py`，提交 `836d7687`：

1. `timeline_origin` 初始化为 `None`，在 begin 同步 sleeper 之后立即按
   `clock() - initial_elapsed_ms/1000` 锚定，并记一条 `timeline_anchor` 事件
   （含 `initial_elapsed_ms`、`anchored_before_reveal`）供后续 capture 核对。
2. 原先无条件执行的锚定改为兜底：仅当 `timeline_origin is None`（静态窗口协议没有 begin 步骤）
   时才在原位置计算，保持旧协议行为不变。
3. `WORLD_BOSS_WINDOW_REVEAL_INTERVAL_SEC = 5.0` 改名为
   `WORLD_BOSS_WINDOW_REVEAL_MIN_INTERVAL_SEC = 1.0`。旧值是**每一次** reveal 等待的下限，
   在动态窗口协议下会把 reveal 阶段拖长数十秒，正是被抹掉的那段时间的主要来源。
   1.0s 对应连续轮询上限 60 req/min，仍在全局 90 req/min 限流之下，且上层循环继续受限流器约束。

不改动协议、不重放已关闭事件、不调整结算时长策略。

## 回归测试

`tests/test_world_boss_miniapp.py` 新增 `WorldBossTimelineAnchorTests`：

- `test_reveal_time_counts_against_the_battle_clock` —— reveal 期间推进假时钟，
  断言窗口按战斗时钟判定过期。
- `test_timeline_anchor_event_is_emitted_before_reveal` —— 断言锚定事件在 reveal 之前发出。

两个用例均做过 revert-check：把 `world_boss_miniapp.py` 的修复回退后确认转红。

第一版回归测试曾用 `first_window_center_ms=500`，低于 `WORLD_BOSS_PERFECT_HOLD_MIN_MS = 520`，
窗口在有无修复时都会被跳过，用例没有鉴别力。revert-check 抓到后改为 `1200`。

同时修正了既有用例中一条空断言：`assertGreaterEqual(clock.now, 30.0)`，
而 `FakeClock` 初值就是 `100.0`，恒真。改为对常量做时间跨度测量。

## 遗留

8/31 04:43 主服务出现一次 `NRestarts=0` 的重启，即有人显式执行了 `systemctl restart`，
非本次部署所为，尚未确认发起方。与当日 13:42 的世界 Boss 事件时间上不冲突，
capture 也未见异常，但仍应查明。
