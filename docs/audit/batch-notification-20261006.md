# Batch Completion Notification

Base: `17bc06d6`. `619562f5` deployed and pushed. Review passes below are separate maintainer passes, not independent
external reviews.

## Evidence And Scope

At 2026-10-06 01:28 CST, one scheduled trial batch finished 12/12 successfully.
The same completion branch sent two adjacent confirmed notifications:

- Completion: `fb348a46759d4b14a96a9f9846376637`, 84 UTF-16 units, 2 lines.
- Outcomes: `22fcb133bbf14613b1db0af865b13acf`, 64 units, 3 lines.

These are not separate daily and batch reports. In
`model/ui.py::_run_cave_public_entry_batch`, the completion call was immediately
followed by a second outcomes call. Combine the existing header and outcome
lines before calling the existing sender once. Keep the 1200-unit ceiling for
material outcomes and the 260-unit ceiling when there are no outcome lines.
No new formatter, sender, retry mechanism or state fields.

No changes to start/progress notifications, shared transport, priority,
batch scheduling/persistence, pause/exception paths, failed-step-only retries,
unknown-operation holds, identity settings or Tianxing evidence gates.
This closes the adjacent terminal-message duplication only, not all notification
debt. Formatting remains bounded/folded by the shared notification path.

## Acceptance And Review

- Reproduced before fix: mixed success, partial manual trial and scheduled trial
  each called audit four times instead of three (start, progress, completion).
  The no-outcomes case already used one terminal notification.
- Focused batch and summary tests: 149 passed, 2 subtests.
- Full frozen suite: **15910 passed, 1438 subtests**, 454.27s.
  JUnit: `/tmp/xiuxian-batch-notification-20261006.xml`.
- Second review: 333 passed, 2 subtests, covering notification acceptance,
  bounded HTML, trial lifecycle/checkpoints/unknown operations and UI ownership.
  Ruff, compileall and `git diff --check` pass.
- Re-read the full completion/early-return flow: persisted completion still
  precedes delivery; manual partial failure keeps failed counts and known gains;
  scheduled failure and unknown branches return before this completion message.
  No automatic notification replay added. No blocker found for this narrow fix.

## Live Observations During Validation

- Main service, observer and watchdog active, `NRestarts=0`. Pending game queue
  empty; the historical held summary is preserved for delivery review.
- 01:45:16 ordinary digest confirmed, receipt
  `394fab98f2a64519bbf88cfd505eafd7`, 1220 UTF-16 units, 23 lines, no mentions.
  This validates the earlier summary work, not this undeployed batch patch.
- WA's fifth protected wild result confirmed 01:43:12; subsequent prediction
  reply 1282517 confirmed 01:43:17. Continue observing remaining runs; do not
  restart during an active request or claim all daily actions completed.
- mudamuda0 naturally continued from its saved fate interpret at 01:28, without
  repeating draw/interpret. Deep settlement and restart confirmed at 01:42:54
  and 01:44:45. Shutdown drain and durable fate-wait scheduling remain debts.

## Release Gate

Back up live and summary SQLite databases, preserve all identity/module
switches and MiniApp configuration, fast-forward only this worktree, then use
one controlled restart in a quiet window. Roll back code only if needed; do not
restore old gameplay state or replay held notifications.

Next scheduled trial window is 05:00 CST. Natural single-completion delivery
must be recorded after release; do not run an extra batch for acceptance.

## Production Checkpoint

WA completed all eight natural wild actions by 01:51:15: seven gains of 45000
cultivation and one change-fate escape, total +315000. Next wild action is
2026-10-07 01:13:49; rift remains 2026-10-06 06:49:44 with future preparation.
No pending game commands or unresolved native fishing rods before stop.

Old worker exited cleanly 01:52:57, without the previous incomplete-cleanup
message. Fast-forwarded this worktree, reran isolated tests (220 passed,
2 subtests), started service 01:54:42, bootstrap complete 01:55:01, then pushed
to `xiuxian-mian/main`. One successful quiet stop does not close the broader
shutdown-drain design debt.

Snapshots (0600, quick_check=ok):

- `/root/xiuxian-stopped-before-batch-notification-20261006.db`
- `/root/xiuxian-summary-stopped-before-batch-notification-20261006.db`

Compared all 24 identities, 59 enable columns across identity/module/runtime
tables, wild strategy and complete MiniApp configuration: unchanged. The held
summary is byte-for-byte equivalent after JSON decoding. Main, observer and
watchdog remain active with NRestarts=0; watchdog ok. Observer's remaining
warning is the existing delivery-review batch, not a new overdue queue.

Natural batch notification still awaits the 05:00 window. No extra trial,
fishing cast, Boss activation or notification test was sent for validation.

## Natural Completion Acceptance

The scheduled second wave `cave_public_1791234003_12` ran naturally from
05:00:03 to 05:12:59 on October 6. All 12 identities completed, with 36
settlements and +516 Tianji remnants. The completion and material outcomes
arrived in one confirmed Bot delivery, receipt
`b4fc54798e714e97a00b353c8c9d730f`: 119 UTF-16 units, three lines, no mentions.
The wave persisted today's completion with retry_at=0. This closes the
adjacent-terminal-duplication acceptance for `619562f5`.

It does not close all batch notification debt: the summary checkpoint still
contained 12 child trial results and three successful progress messages after
the combined completion. Two unrelated tower results brought the pending
count to 17, due 05:30:59. Follow-up is tracked in
`batch-progress-notifications-20261006.md`. No checkpoint was edited or replayed.
