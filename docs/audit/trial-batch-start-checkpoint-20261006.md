# Trial Batch Start Checkpoint

Lab base: `2cd1108f`. This is an admission fix, not a reward-ledger migration.
Production services and game configuration were not changed for verification.

## Finding

`ui_start_cave_public_entry_batch()` could create a background worker without
persisting the daily batch prefix. Its scheduler subsequently wrote a running
snapshot using `_persist_trial_daily_batch_state()`'s non-strict default, so
`False`, `None`, and truthy non-boolean storage results did not prevent work.
There was also a second writer for the startup snapshot after the awaited
start call; it could overwrite a newer result returned by that owner.

Ordinary task scheduling does not prove a production race occurred. Tests
exercise the unsafe API boundary, including an owner that has already
completed before returning. No current live storage failure or duplicated
trial is asserted from this source finding.

## Change

- Daily batch admission claims the existing process-local batch slot and
  requires `save_state() is True` before creating the worker. Manual batches
  without a daily context keep their existing behavior.
- The existing persistence helper stores the same wave, day, steps, failure
  prefix and cumulative outcome fields. No new schema or durable ledger is
  introduced. The scheduler's redundant startup write is removed.
- If start persistence is unconfirmed, no task or game request is created.
  Only this wave's previous fields and shared trial-status fields are restored
  in memory; unrelated configuration remains. Diagnostic errors cannot leave
  the original batch claim stuck in `running` or expose raw storage text.
- Cancellation before worker execution and task-creation failure retain the
  known prefix as retry-pending using explicit save acknowledgement. Ownership
  checks protect a replacement batch, including the check after persistence.
  These paths send no acceptance or recovery notification.
- The admitted worker uses the existing child operation, cooldown, request
  budget, cancellation/drain and notification behavior. Completed identities
  in a valid resumed prefix are not replayed by this change.

## Limits

This does not make an unsuccessful disk write durable. In-memory restoration
is not a database rollback, especially if storage reports failure after a
commit. Restart handling of historical `running` records, ordinary terminal
or paused checkpoint acknowledgements, and durable child-to-parent reward
ownership remain separate debts. No repair is performed against live SQLite.

The parent still aggregates cumulative outcomes, not immutable child receipts.
Child notifications remain enabled until the handoff plan's ownership,
idempotence, capacity and delivery requirements are implemented. This patch
does not resolve the earlier unknown notification batches or modify Attempt.

## Verification

Before the fix, 8 new boundary tests failed and the manual-batch control passed.
Current focused regression: 177 passed / 2 subtests. Tests cover strict save
results and exceptions, a resumed prefix, configuration-read failure, unrelated
configuration edits, replacement claims, startup cancellation, task-creation
failure, real asyncio worker completion with mocked game actions, and exclusion
of the scheduler's stale second write. The resumed-worker fixture uses the
existing outcome builder so it tests a real aggregate shape, not an incomplete
ad hoc dictionary.

Final full regression: 16586 passed / 1461 subtests in 462.32 seconds;
JUnit `/tmp/xiuxian-trial-batch-start-checkpoint-20261006.xml`.
Second cross-module review: 411 passed / 194 subtests, including scheduler
ownership, persistence flags, MiniApp admission and child checkpoint/receipt
contracts. Ruff, compileall and diff checks pass. Runs use
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`; these new tests
mock game actions and persistence. This is not a claim of a suite-wide network
sandbox or of natural production acceptance.
