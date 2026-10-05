# Batch Progress Notification Follow-up

Base: `1def52f6`. Worktree: `/root/xiuxian-batch-progress-20261006`.
Status: `f7958deb` deployed and pushed to `xiuxian-mian/main`.

## Evidence

The natural 05:00 second trial wave completed 12/12 at 05:12:59, with 36
settlements and +516 Tianji remnants. The merged completion was confirmed
once. The summary checkpoint then contained 17 rows: 12 child trial outcomes,
three success-progress messages at 5/12, 10/12 and 12/12, and two tower results.
The batch start had also produced a separate confirmed Bot notification.
These are redundant event layers, not duplicate game execution.

The pending count later reached 18. The ordinary digest was confirmed at
05:31:00, receipt `438b2f7266684f6cbaebde1b44f377c8`, with 1550 UTF-16
units, 20 lines and no mentions. Pending became zero; the one historical
held delivery remained. This is old-code baseline evidence, not acceptance
of the candidate's reduction.

## Narrow Change

`model/ui.py::_run_cave_public_entry_batch` now logs batch start and successful
progress locally through the existing `console_log`. The Web progress state
is still updated for every completed step. Failed-step notifications retain
their normal priority; unknown-result holds, pauses, upstream errors and final
outcomes retain their current paths. No shared sender, summary schema,
scheduler, quota, identity switch, result persistence or retry change.

Child material notifications are deliberately not suppressed by this patch.
Partial failure, upstream abort and pause paths do not yet emit one reliable
aggregate of all earned resources. Suppressing children requires a durable
reward handoff and cancellation/restart tests, not just a `notify=False`
flag. This remains open and must not be described as complete deduplication.

## Verification

The focused pre-existing batch/lifecycle tests passed: 186 tests, two subtests.
Added a 12-step regression verifying all steps and UI progress still finish,
start/progress remain local at the original milestones, and the only batch
notification is the final 36-settlement/+516 outcome. Partial failure asserts
the unchanged failed-step notification and final material summary.

Frozen full-suite acceptance: **15991 passed, 1449 subtests**, 457.08s,
JUnit `/tmp/xiuxian-batch-progress-full-20261006.xml`.
Second maintainer review: **561 passed, 18 subtests**, including trial
checkpoints/restarts, notification delivery/store, UI ownership and shutdown.
These are two passes by the same maintainer, not independent external review.
Ruff, compilation and `git diff --check` pass. Re-read the shared logger:
local scope and 180-character console budget match the previous audit logger.
No failed-step, cancellation, pause, completion or result-persistence path
was removed. All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no synthetic game
batch was sent.

Before rollout, all four native fishing/supply journals are accounted and
all 24 stored trial checkpoints complete. No game pending or active Boss;
WA rift preparation is due around 06:39:44. Back up the game and summary
databases, fast-forward this worktree only and preserve current switches.
Rollback is code-only; do not restore old game state or replay held delivery.

## Production Checkpoint

The explicit stop began at 05:34:14; worker exited successfully at 05:34:21.
After fast-forward and post-merge isolated regression (211 passed, two
subtests), main started 05:35:57 and bootstrapped at 05:36:15. Supervisor
2207210 / worker 2207227 are active, NRestarts=0. Observer/watchdog were not
restarted. Their brief stop warnings correspond to this controlled rollout.

SQLite snapshots, both quick_check=ok and mode 0600:

- `/root/xiuxian-before-batch-progress-20261006.db`
- `/root/xiuxian-summary-before-batch-progress-20261006.db`

Compared 24 identities and 62 control/strategy columns, including automatic
reacquisition and wild strategy, plus full MiniApp configuration, identity
account mapping and global pause controls: no differences. The historical
held list is identical. Startup staggered one already-due timer as designed;
runtime timestamps are not asserted identical. Only the auto-learned quiz
bank remains dirty, preserved uncommitted.

05:37:23 watchdog is healthy; observer still reports the known cancelled
backup-era fight and one held delivery. Game pending is empty and WA rift
preparation remains around 06:39:44. No new game batch or notification test
was forced. Natural absence of start/progress TG notices awaits the next
scheduled batch; the 05:00 wave validates the earlier completion merge only.
