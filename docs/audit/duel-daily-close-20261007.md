# Duel Daily Closure

## Incident And Containment

At 06:13:17 CST on October 7, Lpprceqei / 7538826434 completed its configured
10-attempt batch. The runtime scheduled October 8 01:38:41, then changed to
1/10 and October 7 06:31:22. There were five actual October 7 attacks, not ten:
the batch carried five earlier completions. Current-day commands/finals in
group -1002083016447 are 1290017/1290019, 1290048/1290050,
1290085/1290087, 1290142/1290144 and 1290172/1290176.

At 06:22:06 the existing authenticated control endpoint disabled only this
identity's duel switch. No global pause, equipment operation, manual duel or
other identity change was performed. The 06:31 extra attack did not occur.
This is temporary containment, not a new user configuration policy.

## Cause And Scope

Batch completion consumed an observed baseline of four. Reconciliation then
found the fifth terminal report, counted it as one new attempt, and shortened
the next-day schedule through the stale-timer correction branch.

The fix persists `duel_daily_completed_day` in identity runtime state, including
fresh schema and migration defaults. Completion closes the local game day.
Late evidence still updates observations, resources and shared target cooldowns,
but does not add next-batch progress or pull tomorrow's schedule forward.
The scheduler checks closure after equipment restoration. Explicit progress
reset/clear retains its existing reset semantics; ordinary configuration edits
do not erase closure. The marker expires by game-day comparison, not a new
rolling-24-hour interpretation of the game's per-target limits.

Second review found a related field-ownership bug: replay of a newer final
report overwrote `duel_last_result`, which also stores equipment restoration
phases. The report anchor now advances without replacing a restoration phase
or its diagnostic. This keeps restoration running before the closure guard.
The test uses the actual reconciliation and restoration path, not a mocked
restoration return value. No transport, retry, target-CD, resource-reserve,
Tianxing preparation or CommandAttempt ownership is changed.

## Validation

- The initial replay fixture failed by pulling tomorrow's deadline forward.
- Before the restoration correction, the real `restore_needed` path lost its
  phase to the final-report summary. Updated tests cover both starting restore
  and waiting for an equipment reply, without sending extra commands.
- Initial full regression: 16746 passed / 1484 subtests.
- Final focused and cross-module regression: 547 passed / 23 subtests.
- Separate second-review selection: 392 passed / 5 subtests.
- Persistence test saves and reloads the marker using temporary SQLite.
- Final full regression: 16747 passed / 1486 subtests, 445.53 seconds;
  `/tmp/xiuxian-duel-daily-close-final-20261007.xml`.
- Production-directory checks and runtime acceptance are pending below.
- Ruff, compileall and diff checks pass. All tests use isolated state with
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; no test uses the production database.

## Deployment Boundary

The incident predates the new marker; disabling cleared its pending/timer
state. Re-enabling without correction would start another batch today.
The prepared one-shot recovery checks all five sent anchors and the official
final report, requires a stopped service and absent worker, takes a private
SQLite backup, and changes only this identity's completion day, progress,
consumed baseline, switch and next schedule. Other identity rows and its
target/count/reserve/window/Tianxing configuration must compare unchanged.

Do not confuse merged code with loaded runtime, or the completed offline replay
with tomorrow's natural batch acceptance. The initial deployment plan protected
WA's 06:56 preparation / 07:06 rift window. Its duel remains independently off.

## Deployment And Initial Observation

Commit `51c2e348` was fast-forwarded into production main and pushed only to
`xiuxian-mian/main`. Production-directory isolated tests passed 224 / 18 subtests.
Service stopped cleanly at 06:52:42 with no pending reply tasks and no forced
worker termination. The checked correction applied at 06:53:16, backed up to
`/root/xiuxian-live-backups/duel-close-20261007-065316/state-before.db` with its
private `receipt.json`. Comparison verified all other identity rows unchanged;
the schema adds the empty marker for other identities.

The service started at 06:53:17 and completed 24-identity bootstrap at 06:53:35.
Supervisor 2901469 / worker 2901475 now load `51c2e348`, also loading previously
staged trial commits `514c3a97` and `7f418377`. At 06:54, Lpprceqei's switch is
on, marker is October 7, progress is zero, observed baseline is five, and next
duel remains October 8 01:38:41. No additional duel or equipment command exists
after the five original attacks. This is startup/containment acceptance, not
tomorrow's natural daily-boundary acceptance.

Watchdog is healthy; observer again reports only the same two historical held
notification batches. Its one inactive-service warning occurred inside the
documented maintenance window. Main listener is active, standalone listener
remains disabled. WA/Baji duel and incense refining remain off; harvest stays on.
No global pause, Attempt control change or notification replay was performed.
Foreground observation 78668 continues through WA's preparation/rift window.
