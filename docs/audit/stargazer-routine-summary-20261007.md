# Stargazer Routine Notifications

Base: `449f3efc`. Lab: `/root/xiuxian-stargazer-routine-summary-20261007`.

## Scope

Natural stargazer collect/pull successes still produced independent Telegram
notices because `_finish_stargazer_miniapp_result` promoted every changed
result to `normal`. This conflicts with the current request to batch ordinary
activity while retaining important independent notices.

Only successful `wait`/`inspect` results with a farm snapshot, no error/unknown
flag and no Retry-After now use the existing `low` summary route. Keep the
previous notification path for changed nonroutine results (`action_limit`,
unknown status, partial/error/backoff evidence, missing farm snapshot).
The failure branch is untouched. Empty observations were already low priority.

No request, action, inventory delta, timer or game switch changes. No new summary
kind or schema: old readers can consume the same ordinary pending rows. These
are per-result details within the existing folded summary, not a new daily
reward ledger. Existing summary capacity and unknown-delivery policy still
apply; historical held batches must not be replayed.

## Validation

- Three regressions fail before routing changes: both previous realtime-success
  assumptions and actual transport invocation in the ordinary success path.
- Scoped stargazer, lifecycle, notification, store, delivery policy, entry and
  cave tests: 407 passed / 23 subtests, before the final missing-snapshot guard
  assertion and folded-render assertion were added.
- Acceptance uses real `send_audit_log` and `AuditSummaryStore` on temporary
  SQLite, with transport stubbed: gains applied once, queue persisted, no
  immediate send, one folded delivery after the existing window. Failure with
  already-confirmed collection gains still uses immediate notification.
- Frozen full regression: **16684 passed / 1484 subtests**, 456.20 seconds;
  JUnit `/tmp/xiuxian-stargazer-routine-summary-20261007.xml`.
- Second maintainer review checked callers, partial/cancelled ownership,
  failure routing and durable summary behavior. Separate regression selection:
  **392 passed / 17 subtests**, including fate waiting, background scheduling,
  stargazer lifecycle and summary storage/delivery/health. Not an external review.
- Ruff, compileall and diff checks passed. No production test messages, manual
  game actions, restarts or live-state corrections were performed.

## Acceptance Boundary

Merge only after validation; leave runtime loading to normal maintenance.
Verify a natural stargazer result after the new worker starts and its later
summary delivery. Disk merge is not natural acceptance. This does not close
other legacy senders, trial child-to-parent receipt handoff or unknown delivery
debt. Roll back source if needed, not game state or the durable summary queue.
