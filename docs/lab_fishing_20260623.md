# Fishing Lab 2026-06-23

## Scope

This document started as the lab evidence record for the new fishing module and
was later promoted into the runtime implementation in commit `29e1ac1`.

Current production status:

- Runtime module: connected through `model/features/fishing_runtime.py`.
- Pure parser/planner layer: `model/features/fishing.py`.
- Pure state-machine layer: `model/features/fishing_behavior.py`.
- UI controls: connected through `model/web/static/js/fishing_ui.js` and
  `/api/fishing-config`.
- Scheduler/reply routing: connected through the normal Xiuxian app loop.
- Production service: restarted after tests on 2026-06-23 22:51 UTC+8, then
  repaired again after the 22:59-23:00 live failure replay.

This file is therefore not a "report-only" artifact anymore. It is the evidence
and rollout record for the live fishing module.

## Real Text Evidence

- `data/messages/2026-06-23.log:5144-5146`: `.渔具铺` shows bait and pond rules.
- `data/messages/2026-06-23.log:5191-5192`: `.打窝` usage lists `米糠小窝|灵草窝|妖腥窝`.
- `data/messages/2026-06-23.log:5211-5213`: `.打窝 灵草窝` succeeded, but the reply only says `【打窝已成】` and does not disclose cost.
- `data/messages/2026-06-23.log:6648`: `.打窝 灵草窝` failed with `打窝失败，资源不足：item_fishing_bait_spirit_ricex3。`, proving `灵草窝` consumes three `灵米饵`.
- `data/messages/2026-06-23.log:6695`: `.打窝 米糠小窝` failed with `打窝失败，资源不足：item_fishing_bait_plainx2。`, proving `米糠小窝` consumes two `凡饵`.
- `data/messages/2026-06-23.log:6698`: `.打窝 妖腥窝` failed with `打窝失败，资源不足：item_fishing_bait_demon_bloodx2。`, proving `妖腥窝` consumes two `妖血饵`.
- `data/messages/2026-06-23.log:57544`: latest `.渔具铺` explicitly lists full chum costs:
  `米糠小窝=凡饵x2+灵石x30`, `灵草窝=灵米饵x3+凝血草x5`,
  `妖腥窝=妖血饵x2+一阶妖丹x3+灵石x200`.
- `data/messages/2026-06-23.log:712-713`, `:746-747`, `:2609-2613`: `.鱼篓` is the safe manual before/after comparison surface. It shows rod ownership, fishing skill, daily rod count, current chum, bait counts, and fish counts.
- `data/messages/2026-06-23.log:671-675`: `.买鱼饵` usage is `用法：.买鱼饵 <凡饵|灵米饵|灵虫饵|妖血饵|月华饵> [数量]`.
- `data/messages/2026-06-23.log:743-745`, `:764-767`: `.买鱼饵 凡饵*5` is invalid; quantity is a separated argument, for example `.买鱼饵 凡饵 5`.
- User-provided WA sample at `2026-06-23 01:37`: `.钓鱼 青溪浅滩 灵米饵` starts `静候鱼讯`; `.钓鱼状态` reaches `鱼在试口`; `.试探咬饵` reaches `正口黑漂`; `.提竿` catches `银须灵鲢`; `.开鱼 银须灵鲢` is logged as `[2026/6/23 01:37] 韩天尊: 【剖鱼取机缘】` and returns `灵石x28、灵鱼肉x1、灵鱼鳞x1、清灵草x1、修为+39`.
- `data/messages/2026-06-23.log:214-216`, `:252-260`: `.买鱼饵 <鱼饵> [数量]` success replies use `【渔具铺】\n你购得 【凡饵】x10。` and `你购得 【灵虫饵】x1。`.
- `data/messages/2026-06-23.log:235-242`: fishing without bait replies `你的鱼篓中没有【灵虫饵】。可用 .买鱼饵 灵虫饵 购买。`
- `data/messages/2026-06-23.log:55378-55429`: live failure replay. The
  automation sent `.钓鱼 青溪浅滩 灵米饵` at 22:59:01, got a start panel at
  22:59:03 with `预计 43秒`, then incorrectly sent another `.钓鱼` at
  23:00:14. The bot replied `你已有一竿尚未收起`, status at 23:00:34 showed
  only `提竿剩余：6秒`, and the delayed lift at 23:00:40 ended in `【空竿】`.

## Cost Decision

Known:

- `米糠小窝`: `凡饵 x2` plus `灵石 x30`.
- `灵草窝`: `灵米饵 x3` plus `凝血草 x5`.
- `妖腥窝`: `妖血饵 x2` plus `一阶妖丹 x3` and `灵石 x200`.

Known bait purchase costs from the shop text:

- `凡饵`: `灵石 x12`.
- `灵米饵`: `灵石 x35`.
- `灵虫饵`: `灵石 x90` plus `凝血草 x2`.
- `妖血饵`: `灵石 x220` plus `一阶妖丹 x1`.
- `月华饵`: `灵石 x650` plus `二级妖丹 x1`.

## Automation Decision

First fishing automation must fail closed for unproven or unavailable resources:

- Do not auto-send any chum whose exact cost has not been proven by source or a resource-shortage failure sample.
- `.钓鱼` requires one selected bait; `.打窝` requires the selected chum bait cost above.
- If a local basket/storage snapshot proves bait is insufficient, block unless `auto_buy_bait` is explicitly enabled.
- If `auto_buy_bait` is enabled, the command plan prepends `.买鱼饵 <鱼饵> <配置买饵数量，默认8；若缺口更大则按缺口>` before `.打窝` and `.钓鱼`.
- If the same local snapshot proves `灵石`, `凝血草`, `一阶妖丹`, or `二级妖丹`
  is insufficient for planned bait purchases or chum, block before sending.
- `.试探咬饵` is controlled by `auto_probe`; default is off because the benefit is still uncertain.
- Parser/state work may observe `.渔具铺`, `.鱼篓`, `.买鱼饵`, `.钓鱼状态`, `.试探咬饵`, `.提竿`, `.开鱼`, and chum replies from local text only.

Runtime chain after the 2026-06-24 fix:

1. Send `.钓鱼 <鱼塘> <鱼饵>`.
2. Parse the start panel `预计 N秒 内会有鱼讯`.
3. Schedule `.钓鱼状态` for that due time using the fishing short-window follow-up task.
4. If status says `鱼讯已至`:
   - with auto probe off, schedule `.提竿` after a bounded human-like delay;
   - with auto probe on, schedule `.试探咬饵` after a bounded delay, then
     schedule `.提竿` after the `正口黑漂` reply.
5. If lift succeeds, add that fish to `fishing_pending_open_fish` and schedule
   the next rod after a post-rod delay. Do not open fish immediately.
6. When the daily rod limit is reached and there is a pending fish queue, open
   queued fish after a bounded delay with `.开鱼 <鱼名>`. Keep the queue until
   a real `【剖鱼取机缘】` reply removes the opened count.
7. If the reply is `你今日已垂钓 20/20 竿，神识已乏，明日再来。`, open pending
   fish first if the queue is non-empty; otherwise schedule the next attempt
   for the next local day and do not retry the current rod.

Terminal/recovery rules after the live failure replay:

- `【空竿】` is terminal; schedule the next rod after a short post-rod delay.
- `【提竿成功】` is terminal for the rod; queue the prompted fish locally and
  continue the rod cycle without sending `.开鱼` immediately.
- `你已有一竿尚未收起` means the previous `.钓鱼` collided with an active rod;
  recover with `.钓鱼状态`, not another `.钓鱼`.
- A swallowed reply or timed-out active rod recovers with `.钓鱼状态`.
- `.钓鱼状态`, `.试探咬饵`, `.提竿`, and `.开鱼 <鱼名>` are short-window
  whitelist commands, but they must be reached through the module's own
  follow-up timing; no reply path should fire them synchronously. `.钓鱼`,
  `.买鱼饵`, and `.打窝` stay on the normal global send gap.

The scheduler may open `fishing_pending_open_fish` only after the configured
daily rod limit has been reached or an explicit daily-limit reply arrives. The
field is a JSON fish-count queue; legacy single-fish values are still accepted
as one pending fish for recovery.

## UI Configuration Contract

Fishing strategy must be operator-selectable per identity, not hardcoded:

- Fish pond: `青溪浅滩`, `灵眼寒潭`, `乱星海礁`.
- Bait: `凡饵`, `灵米饵`, `灵虫饵`, `妖血饵`, `月华饵`.
- Auto chum: disabled by default.
- Chum type: `无`, `米糠小窝`, `灵草窝`, `妖腥窝`.
- Auto buy bait: disabled by default.
- Auto probe bite (`.试探咬饵`): disabled by default.

If auto chum is enabled but the selected chum cost is not proven, the command
plan must block before sending both `.打窝` and `.钓鱼`.

If the selected bait or chum bait is missing from the local basket/storage
snapshot, the command plan blocks unless auto buy bait is enabled. With auto buy
enabled, the planner buys only the missing bait count first.

## Chum Cost Probe Protocol

Coordinate with an operator before any live spend:

1. Pick one identity with `当前窝料：无`.
2. Run `.鱼篓` and save the reply.
3. Run exactly one `.打窝 灵草窝`.
4. Run `.鱼篓` immediately again.
5. Compare bait counts and `当前窝料`.
6. If `.鱼篓` does not expose the consumed material, use a controlled shortage
   sample from a low-resource identity to obtain the internal `item_keyxN`
   failure text.

## Runtime Structure

The live module follows the three-layer structure used by the Rust reference:

- Parser/planner: `model.features.fishing`.
- Behavior/state-machine: `model.features.fishing_behavior`.
- Runtime adapter: `model.features.fishing_runtime`.

UI and app routing should stay thin. They may expose configuration and route
messages, but command-chain decisions belong in the parser/behavior/runtime
layers above.

## Rollout Verification

Before the 2026-06-23 rollout, these checks passed:

- `.venv/bin/python -m pytest tests/test_fishing.py tests/test_fishing_runtime.py tests/test_fishing_ui.py tests/test_fishing_app_routing.py -q`
- `.venv/bin/python -m pytest -q`
- `.venv/bin/python -m compileall -q model tests tools`
- `git diff --check`
- `node --check model/web/static/js/fishing_ui.js`

Production restart evidence:

- `xiuxian.service`: active after restart.
- `xiuxian-safety-watchdog.service`: active after restart.
- Startup log showed UI on `0.0.0.0:3030` and automation startup success.
- Live DB check showed no enabled fishing rows and no pending fishing action/open
  fish residue immediately after rollout.

Follow-up verification after the 22:59-23:00 failure replay:

- `.venv/bin/python -m pytest tests/test_fishing.py tests/test_fishing_runtime.py tests/test_safety_watchdog.py -q`
  passed with `145 passed`.
- `.venv/bin/python -m pytest tests/test_fishing.py tests/test_fishing_runtime.py tests/test_fishing_ui.py tests/test_safety_watchdog.py -q`
  passed with `152 passed`.
- `.venv/bin/python -m pytest -q` passed with `1547 passed, 338 subtests`.
- `.venv/bin/python -m compileall -q model tests tools`, `node --check
  model/web/static/js/fishing_ui.js`, and `git diff --check` passed.
- Production service restarted again on 2026-06-24 00:25 UTC+8. Journal showed
  UI on `0.0.0.0:3030`, automation startup success, 22 identities, and no
  traceback/error match in the post-restart startup window.
- Live DB check after restart: `global_enabled=1`, `identity_module_state`
  fishing enabled rows `0`, `identity_runtime_state` fishing enabled rows `0`,
  and fishing pending rows `0`.
- `.venv/bin/python tools/safety_watchdog.py --once --dry-run` returned
  `watchdog ok`.
- Added coverage for in-progress rod recovery, swallowed/no-active replies,
  open-fish non-blocking behavior, late open-fish settlement preserving a new
  active rod, open-fish send failure not blocking the next rod, resource
  shortage preflight/storage deltas, narrow short-window send-gap exemptions,
  and watchdog negative cases proving `.钓鱼` is not exempt.
