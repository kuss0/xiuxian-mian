# Fate Progress Diagnostic

Base: `aaf91d44` (accepted fishing-wait notification candidate included).
Status: `46d79e54` deployed and pushed with `aaf91d44`.

## Incident And Uncertainty

At 2026-10-06 02:06:46 CST, xianxia9527 (Yinluo) stopped with
`fate_progress_regressed`. Its verified saved quest is `accept_v1`,
`cultivation_gain`, active, progress 5/30. No pending action or unconfirmed
prerequisite. The attempt only loaded dwelling, opened the external entry and
read fate `/start` (all HTTP 200); it sent no draw/interpret/choose/settle.

Existing captures retain body shape/digest but not the rejected numeric
progress. The current guard proves that the parsed incoming value was below 5,
not why it decreased. Net cultivation, upstream staleness and other state
changes remain hypotheses; do not remove monotonicity protection on that basis.

## Narrow Change

When `accept_state` rejects `fate_progress_regressed`, log only previous
progress, observed progress and target, plus the existing identity prefix.
Keep the same error and all authority/record/day/quest checks, with no state
write, additional request, TG notification or replay. No tokens, record keys,
quest descriptions or raw payloads are logged.

## Validation

- Regression reproduces the missing diagnostic before the patch, while the
  business guard already correctly blocks mutations in both versions.
- Focused fate, cave and fishing integration: 254 passed, 5 subtests.
- Combined frozen full suite: **15920 passed, 1438 subtests**, 453.57s.
  JUnit `/tmp/xiuxian-fate-progress-diagnostic-20261006.xml`.
- Separate maintainer review pass: 381 passed. Re-read all error branches and
  checked that the new log is only on an already-rejected path, with the old
  snapshot preserved and no action/notification/storage calls in the regression.
  No independent external review implied. Ruff/compileall/diff checks pass.

This accepts the diagnostic only; the underlying progress incident is open.
Deploy together with `aaf91d44` after normal summary delivery. Preserve identity
controls, timers, the held notification and all game state. Observe the next
natural read, not an extra active probe. Code rollback must not restore an old
game database.

At 02:13:50 Lpprceqei finished its remaining wild runs and advanced to tomorrow.
At 02:24:44 ordinary summary confirmed (receipt
`00622aa782ba40838486b7aed9ac7636`, 1318 UTF-16 units, 24 lines, no mentions).
Normal queue empty; the one historical held batch remains untouched.

## Production Observation

Quiet stop completed 02:28:55 without incomplete cleanup. Fast-forwarded both
accepted commits, post-merge isolated tests 200 passed; start 02:30:46,
bootstrap 02:31:05, push to `xiuxian-mian/main` completed. Main, observer and
watchdog active/NRestarts=0; watchdog ok. The planned service downtime explains
the observer's 02:29/02:30 inactive samples, not a spontaneous restart loop.

Snapshots (0600, quick_check=ok):

- `/root/xiuxian-stopped-before-wait-fate-diagnostic-20261006.db`
- `/root/xiuxian-summary-stopped-before-wait-fate-diagnostic-20261006.db`

All 24 identities, 59 enable fields, full MiniApp configuration, wild strategy
and wild/rift/fishing/duel timers match the stopped snapshot. Historical held
notification unchanged. Runtime quiz learning remains the only production
dirty file; it was not staged or reverted.

At 02:31:21 the normal scheduler supplied the missing evidence:
`previous_progress=5`, `observed_progress=0`, `target=30`. The same guard stopped
the chain with no draw/interpret/choose/settle. Numeric diagnostic acceptance is
complete. The cause of the regression and any required semantic fix remain open;
no claim that lowering the old snapshot or relaxing the guard would be correct.
