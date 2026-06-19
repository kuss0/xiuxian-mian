# Absorb Plan 2026-06-20

## Baseline

- Production checkout: `/opt/xiuxian-main`
- Lab checkout: `/root/xiuxian-main-absorb-20260620`
- Baseline commit: `bffa9ee fix storage bag gift-only item rules`
- Production services are not restarted by lab work.
- Lab verification must use isolated `XIUXIAN_*` paths and must not read or mutate production sessions, state, or DB.

## Source Policy

- Rust line is the primary architecture reference.
- wxjerry line is a feature and wording reference only; do not merge or cherry-pick wholesale because its Python tree diverges structurally from mian.
- Local text, logs, reply context, and observed bot messages remain the source of truth.
- Tianjige/API reads remain optional/manual or explicit fallback, not a default runtime dependency.

## Batch 1: Delivery Semantics

Goal: make the Python line expose the same operational distinction rust now enforces: a Telegram delivery is either a new message or an edited delivery.

Absorb from rust:

- `Delivery{New, Edited}` concept.
- Sequence cursor discipline already present in `message_box.py`.
- Action prompts that should answer only new messages must not be retriggered by edits.
- Final/result handlers that depend on edited replies must continue to consume edits.

Python targets:

- `model/verified_event.py`
- `model/message_box.py`
- routed reply/passive inbox call sites
- tests around message box, passive inbox, dungeon join, jiyin, divination, and concubine heart

Acceptance:

- New and edited deliveries have a stable helper/label available to handlers and tests.
- New-only prompt handlers stay new-only.
- Edited result handlers still work for divination, concubine heart, rift, wendao, and wild training.
- Focused pytest passes in lab before promotion.

Lab progress:

- Added stable `delivery_kind` helpers and properties for `VerifiedGameEvent` and `MessageFact`.
- Exposed `delivery_kind` in message-box snapshot JSON.
- Made the shared claimed-prompt helper fail closed for edited deliveries.
- Focused lab verification passed: `270 passed, 106 subtests passed`.

## Batch 2: Divination / 问天 Exchange

Goal: move remaining redemption logic toward rust's event-driven model and away from timing windows.

Absorb from rust:

- Treasure reveal is captured from tagged/routed bot messages, including edited messages.
- Exchange success is confirmed by event-date/message evidence instead of a racy time window.
- Restart should not lose an already revealed exchange candidate.

Python targets:

- `model/features/divination.py`
- `model/features/storage_bag.py`
- `tests/test_divination.py`
- `tests/test_storage_bag_transfer.py`

Acceptance:

- Existing local item whitelist remains exact: `凝血草`, `凝血草种子`, `清灵草`, `天雷竹`, `妖丹` and other explicitly approved local items only.
- `灵石` is not used as an exchange sacrifice.
- Edited `神物现世` can trigger exchange once.
- Duplicate edits do not duplicate exchange sends.
- Focused pytest passes in lab.

Lab progress:

- Kept edited `神物现世` handling covered by existing tests.
- Preserved exact local listing-marker policy; `灵石` is not a listing marker.
- Changed auto resource refresh policy: Tianjige/API inventory refresh is disabled by default for divination exchange and only enabled with `XIUXIAN_DIVINATION_ALLOW_AUTO_API_REFRESH=1`.
- If the target identity has local storage-bag cache, local cache now completes the direct-exchange/transfer/manual-reason decision before any API fallback is considered.
- Focused lab verification passed: `337 passed, 106 subtests passed`.

## Batch 3: Dungeon / 苍坤 / 副本

Goal: absorb the useful parts of wxjerry and rust around副本队伍 evidence without importing wxjerry's structure.

Absorb from wxjerry:

- Team membership and voting should be based on actual entered/joined evidence, not stale query snapshots.
- Replica snapshot triggers must be limited to the correct source.
- 苍坤 vote identity must be recorded from actual entry evidence.

Absorb from rust:

- Cangkun roster/arm concept.
- Staggered joins/votes.
- Guard against stale fourth-act prompts.
- Dungeon cooldown/status includes 苍坤 and 神识.

Python targets:

- `model/app_replica.py`
- `model/features/join_dungeon.py`
- `tests/test_replica_absorb.py`
- `tests/test_join_dungeon.py`
- `tests/test_log_group_display.py`

Acceptance:

- 苍坤 preview can distribute 神识-capable identities as separate anchors where possible.
- Entered-team records are preferred over query snapshots for later choices.
- Log-group progress/result capture remains truthful.
- No extra active status polling is introduced.

Lab progress:

- Compared wxjerry's recent `app_replica.py` fixes with mian.
- The wxjerry team-snapshot/source fixes target its old auto-team flow; mian's current lightweight button flow does not have the same unguarded snapshot apply path.
- Existing mian already has Cangkun multi-team planning and tests that spread high-spiritual-sense anchors across teams.
- Existing mian already has joined/entered room state, per-stage button guards, Cangkun/Zhuimo/Luoyun progress handling, and log-group display tests.
- No direct code transplant was made for this batch because the structures diverge.
- Focused lab verification passed: `277 passed, 42 subtests passed`.

## Batch 4: World Boss

Goal: audit current world boss behavior against wxjerry's cleanup and our current user policy.

Current target policy:

- 22 roles can be used.
- Per-role sends in a round should be fast, roughly one second apart.
- Full round respects the boss command cooldown, with at least about 70 seconds before another round.
- Missing replies can be retried while waiting, max two retries, then give up.
- Watchdog/whitelist must allow the intended cadence without allowing storms.

Python targets:

- `model/features/world_boss.py`
- `tests/test_world_boss.py`
- watchdog tests where needed

Acceptance:

- No regression in existing 90s/round behavior.
- Missing-reply retry is bounded and visible.
- No global lock/watchdog false fuse in tests.

Lab progress:

- Current mian already implements the requested world-boss cadence: 90s reply/pending window, 1s per-role action gap, at least 70s round gap, max two no-reply retries, and event-burst send metadata for watchdog allowlisting.
- Existing tests already cover 22 enabled identities in one fast round, next-round gap, retry try1/try2 without extra action consumption, and watchdog rejection of malformed or excessive bursts.
- No wxjerry transplant was made for this batch.
- Focused lab verification passed: `95 passed`.

## Batch 5: 灵树新版定脉

Goal: keep old stopped 灵树灌溉玩法 archived, but absorb the newly reopened 定脉 gameplay.

Absorb from wxjerry/rust:

- New 定脉 text recognition and state transitions.
- Default-off or explicit-enable safety where appropriate.
- Edited reply handling if 定脉 progress/result arrives as edits.

Python targets:

- `model/features/tree.py`
- `model/state.py`
- `model/module_manifest.py`
- tree tests and startup-recovery tests

Acceptance:

- Old stopped gameplay remains archived and cannot silently revive.
- New 定脉 is separately gated and observable.
- No startup `.灵树状态` storm.
- No API dependency is added.

Lab progress:

- Confirmed the current codebase already contains the main wxjerry `1918f49` 定脉 implementation shape: `.灵树定脉` panel query, `.定脉 注灵/固脉/净浊` action choice, mature/blocked stop, and final-board harvest handling.
- Changed module contract from "entire 灵树 archived" to "old 灌溉 archived, new 云梦灵眼定脉 active".
- Reconnected tree bootstrap/scheduler to the identity scheduler order, while the scheduler continues to send `.灵树定脉` / `.定脉 ...` only, not `.灵树灌溉`.
- Added compatibility parsing for newer 定脉 log shapes such as `主脉: 木` and `浊气/紊乱`.
- Added replay samples for `tree_panel`, `tree_pulse`, `tree_guard`, and `tree_harvest` so the active module stays sample-complete.
- Focused lab verification passed with world boss/watchdog and contract tests: `195 passed, 3 subtests passed`.

## Promotion Gate

Each batch must pass:

```text
env XIUXIAN_DATA_DIR=/root/xiuxian-main-absorb-20260620-data/data \
  XIUXIAN_SESSION_DIR=/root/xiuxian-main-absorb-20260620-data/session \
  XIUXIAN_STATE_DIR=/root/xiuxian-main-absorb-20260620-data/state \
  XIUXIAN_MESSAGES_DIR=/root/xiuxian-main-absorb-20260620-data/messages \
  XIUXIAN_DB_FILE=/root/xiuxian-main-absorb-20260620-data/state/chaogu_state.db \
  XIUXIAN_LIVE_GUARD_DIR=/root/xiuxian-main-absorb-20260620-data/live-guard \
  /opt/xiuxian-main/.venv/bin/python -m pytest -q <focused tests>
```

Before production promotion:

- `git diff --check`
- `compileall` for `model` and `tests`
- focused pytest for touched modules
- full pytest when changes cross shared routing/runtime boundaries
- production restart only after lab is green
