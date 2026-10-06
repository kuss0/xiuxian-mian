# Trial Batch Cancellation

## Reproduced Defect

`_run_cave_public_entry_batch()` caught `Exception`, not `CancelledError`.
Cancellation at an action, inter-step wait or progress notification bypassed
the batch cleanup. The UI stayed running and the confirmed parent prefix was
not checkpointed. `MiniAppFlowCancelled` inherits `CancelledError`, so even a
child that had safely drained HTTP and retained its own result took this path.

The first isolated replay produced 9 failures / 2 passes. No live batch was
cancelled to produce evidence. Natural production impact has not been proven
for today's completed trial runs.

## Repair Boundary

- A current batch cancelled inside its loop clears its UI running marker and
  checkpoints only the prefix already received and accounted by the parent.
  The original cancellation propagates; cleanup does not send a TG message.
- Cancellation during `ui_run_cave_public_entry()` leaves the current step
  unadvanced and uses the existing same-day unknown-result hold. An exception's
  cumulative reward payload is not treated as a bound child receipt.
- Cancellation between steps preserves the cursor and failed-step list. Normal
  scheduling may resume the remaining suffix after its existing backoff; it
  does not replay the already-confirmed identities.
- A pause/backoff branch that has already closed and saved before notifying
  is left unchanged. Cancelling its notification cannot reset its retry time
  or overwrite its original result. Completed notification cancellation also
  leaves completion intact.
- Cleanup checks the state object, batch ID and start timestamp. A replacement
  batch is not cleared. This is in-process task ownership, not durable
  child-to-parent reward ownership or a replacement for identity/HTTP locks.
- Existing HTTP thread draining remains in place. Tests cancel a real local
  executor flow and keep the batch running until that thread has finished.
- A done callback releases only an untouched pre-start claim if the task never
  entered the coroutine. Creation failures close the unstarted coroutine.

## Persistence

Cancellation checkpoints opt into explicit `save_state() is True`
acknowledgement. Other callers of `_persist_trial_daily_batch_state()` retain
their existing return contract; this is not a general persistence rewrite.

False, None, truthy non-booleans and exceptions are not acknowledgements.
On failure the UI reports unconfirmed storage and the currently staged parent
prefix is held in memory, without a second explicit save or a new game request.
If the config itself is unreadable, cleanup retains the storage warning and
original cancellation instead of claiming a valid checkpoint.

No claim is made that an unsuccessful database write survives a subsequent
process loss. Durable admission/receipt handoff under storage failure remains
in `trial-report-handoff-plan-20261006.md`. This patch neither removes child
notifications nor adds a completed-operation archive or notification outbox.

## Validation

- Initial focused repair: 46 passed.
- Extended cancellation/receipt/admission checks: 229 passed; separate UI,
  background, admission and restart pass: 151 passed.
- Second-review cross-module pass: 402 passed / 11 subtests. Review then found
  that already-saved pause/backoff notifications must not rewrite their state;
  the correction and its regressions passed a further 119 tests.
- Final focused cross-module pass after that correction: 363 passed /
  6 subtests. Ruff, compilation and whitespace checks passed.
- The pre-correction full suite was intentionally stopped after 12782 tests /
  1326 subtests. It is not final acceptance.
- Final full suite: 16293 passed / 1461 subtests, 450.32 seconds. JUnit:
  `/tmp/xiuxian-trial-batch-cancellation-final-20261006.xml`.
- All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. An isolated SQLite meta-codec
  reopen verifies the prefix and same-day hold. No production DB changes,
  Telegram actions, MiniApp calls or UI configuration mutations were made.

The second review was a separate pass by the same maintainer, not an
independent external audit. Runtime loading and natural cancellation behavior
remain unaccepted. The main worker still loads `f7958deb`; no restart is part
of this batch.

At 14:40 the live trial slots still contained 24 complete operations and 72
round receipts; both daily waves were completed for October 6. The old worker
deferred Lpprceqei's uncalibrated duel again to 15:10:54. These observations are
not acceptance of the staged cancellation or duel-calibration changes.

Code `3ce30b48` was fast-forwarded to production disk and pushed to
`xiuxian-mian/main`. Post-merge isolated regression: 189 passed. At 14:45
supervisor 2207210, worker 2207227, observer 2322377 and watchdog 2178662
remained unchanged; only the two historical held-summary warnings persisted.
The only unrelated dirty production file was runtime quiz learning data.
