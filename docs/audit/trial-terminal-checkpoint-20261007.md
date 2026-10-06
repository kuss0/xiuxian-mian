# Trial Terminal Checkpoints

Base: `ac92f563`. Lab: `/root/xiuxian-trial-terminal-checkpoint-20261007`.
Candidate only; no production source, state, switches or services changed.

## Scope

The batch admission and cancellation paths already require an explicit save
acknowledgement. Ordinary completion, shared pause, upstream stop, failed-step
projection and exception exits did not. A failed save could therefore still
produce a completion or resumable-batch notice. This is a reproduced code gap,
not evidence that a production save actually failed.

The batch's existing persistence helper is reused with strict acknowledgement.
On failure the owner retains its full observed prefix and gains in memory,
holds only this day/wave, and reports unconfirmed storage rather than completion.
It makes no second save attempt or game request. Unrelated config changes and
replacement batches are preserved. Successful saves retain existing notification
and business behavior; cancellation retains its original exception.

The saved retry reason is `batch_checkpoint_save_outcome_unknown`. Its unknown
axis is storage acknowledgement, not a fabricated unknown game outcome. The
existing older reader recognizes the hold, so rollback cannot silently turn it
into permission to retry. The in-memory snapshot is also retained when config
access fails, and admission checks that scoped hold before reading config.

## Limits

This does not make a failed database write durable. A later successful ordinary
save can persist the hold; before that, an immediate process loss retains only
the previously committed state and existing child operation receipts. No new
remote recovery or child-to-parent atomic handoff is claimed. Normal per-step
reward ownership, retention and the scheduler's no-enabled-identity marker remain
separate work. Successful child notices are not removed.

## Code Acceptance

- Original code: 18 new regressions failed, two controls passed.
- Scoped candidate: 116 passed before the final day/wave scoping case.
- Seven old outcome-retention tests initially failed because their recording
  mock implicitly returned None. That success fixture now explicitly returns
  True, leaving its existing assertions unchanged. New tests still reject None.
- Temporary SQLite rollback and later save/reload tests distinguish memory
  retention from durable commit. The actual `ac92f563` hold reader is replayed
  against the new marker. Replacement-owner, notification cancellation, config
  failure, and standalone non-daily cases are covered.
- Frozen full regression: **16712 passed / 1484 subtests**, 455.74 seconds;
  JUnit `/tmp/xiuxian-trial-terminal-checkpoint-20261007.xml`.
- A separate maintainer review checked ownership across awaits, saved versus
  in-memory state, compatibility with the previous hold reader, and exception
  boundaries. Cross-module regression: **366 passed**, covering child operations,
  receipt validation, process restart, summary storage and notification delivery.
  This is a second review by the same maintainer, not an external audit.
- Ruff, compileall and diff checks passed. No tests touched live game state or
  made production requests. Runtime loading and natural storage-failure behavior
  are not accepted by these offline tests; do not inject a live save failure.

Ready for scoped merge. Keep production's current worker running through the
ongoing trial, voyage/fishing and duel windows; do not restart solely for this
failure-path change. Record the merge and subsequent load separately.
