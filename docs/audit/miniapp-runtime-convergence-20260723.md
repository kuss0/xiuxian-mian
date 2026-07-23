# MiniApp Runtime Convergence Audit - 2026-07-23

## Scope

This pass converges wild training, tower, and stargazer automation on their
MiniApp execution paths without restoring deprecated group-command fallbacks.

## Removed Runtime Paths

- Wild training command reply cleanup, timeout recovery, resend, and phaseful
  replay scheduling.
- Tower command reply tracking, action-guard ownership, and generic pending
  replay.
- Stargazer active text-command senders, fallback scheduler, due-followup scan,
  and the UI sync command endpoint.
- Deep-retreat delays that existed only to wait for legacy wild-training
  command state.

Persisted `.野外历练 <策略>` and `.闯塔` pending rows are tombstoned on startup
and in the retry scheduler. They are deleted without sending a replacement
command. Command-era reply anchors and action-guard sessions are cleared once
without resetting MiniApp timers or results.

## Retained Compatibility

- Database columns remain for non-destructive schema compatibility but no
  scheduler or send path depends on them.
- Historical message parsing/reporting remains read-only.
- Stargazer passive replies can still synchronize observed slot/cooldown state;
  active execution uses the public MiniApp entry.
- Wild-training strategy, tower daily window, identity switches, MiniApp
  cooldowns, results, and Tianxing route preparation remain active.

## UI

The MiniApp console now uses four focused tabs: runtime overview, public entry,
game state, and diagnostics. It displays per-identity MiniApp state records,
results, replacement commands, entry health, global request budget, and adapter
diagnostics. Desktop and mobile layouts use the existing console form language,
compact checkboxes, bounded modal scrolling, and no horizontal overflow.

## Verification

- Full test suite: `3272 passed, 405 subtests passed` after final UI polish.
- Browser smoke: desktop `1440x1000` and mobile `390x844`, no console errors or
  horizontal overflow; all four MiniApp tabs render.
- Live message log audit for 2026-07-23: no script `sent` event for deprecated
  wild-training, tower, or stargazer action commands.
