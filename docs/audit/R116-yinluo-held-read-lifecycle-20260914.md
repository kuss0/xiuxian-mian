# R116 Yinluo Read Lifecycle Under Resource Holds

Status: scoped offline candidate verified; not production acceptance.
Parent: `rebuild-stability-20260907.md`, R65, R114 and R115.

## Reproduction

The initial isolated suite has 28 failures and eight passing preservation
controls. A resource hold leaves a completed query's shared pending and guard
behind. A book gap additionally prevents its exact, retained native reply from
completing the read operation. Hot and archived completion recovery both stop
at these barriers. Scheduler hold/due-time returns skip read expiry; book gaps
instead fall through to repeated calibration that cannot repair the gap.

Follow-up real shared-admission tests reproduced five additional failures:
the manual sender cleaned pending during preparation but left the old completed
query guard until after sending. The guard rejected that send. Local exact
completion cleanup now runs before preparation/admission too. Shared sending
and guard implementation are not changed.

These are offline reproductions. They do not establish the current production
state or authorize clearing a retained consuming operation.

## Contract

1. A retained, strictly owned native banner-query result may complete its read
   operation despite a financial hold or book gap. The matching receipt and
   terminal outcome must actually exist; an observed balance alone is not
   completion. A capacity failure that prevents receipt storage is not success.
2. The read's exact pending removal shares the normal resource transaction.
   Failed saves restore the facts and pending row together. Existing hot/cold
   ownership, original send-time and archive validation remain mandatory.
   Guard reconciliation uses the existing exact-owner helper and persistence
   contract; it does not introduce an atomic shared guard transaction.
3. This exception is banner-read-only. It does not clear a hold/gap, consume or
   release an uncertain reservation, retire financial history, complete an
   unrelated action, or make held balances available for spending.
4. Enabled scheduler passes may perform local native-log reconciliation and
   read expiry before hold/business-due checks. Active caller/RPC exclusion
   remains first. Log reads keep their existing bounded/due-time policy.
5. Sticky legacy, receipt-conflict and capacity barriers stop automatic new
   actions, including ineffective repeated calibration of a book gap. A manual
   banner read remains subject to existing admission, exact operation capacity,
   shared guard/rate controls and owner checks. No new sender/retry path exists.
6. The hold wait remains visible. Repeated scheduler ticks inside the same
   unchanged wait do not keep pushing its recheck time into the future.
7. Manual dispatch reconciles existing durable completion before entering
   shared admission. This does not bypass remote cooldowns, short-repeat
   guards, other commands' pending work or current owner/control validation.

## Scope And Evidence

Candidate changes are confined to `model/yinluo_accounting.py`,
`model/features/yinluo.py`, tests and audit documentation. No persisted field or
schema is added. Initial reproducer artifact:
`/tmp/xiuxian-r116-initial-20260914.xml`.

- Focused lifecycle suite: 78 passed, 8.84s;
  `/tmp/xiuxian-r116-focused-20260914.xml`.
- Associated Yinluo/Wanxin/resource/transport/recovery suite: 1627 passed,
  311 subtests, 75.93s; `/tmp/xiuxian-r116-related-20260914.xml`.
- Configured repository Ruff, scoped F401/F811/F821/F822/F823/F841,
  compilation, dependency consistency and whitespace checks pass.
- Network-isolated full regression: 12402 passed, 1275 subtests, 212.91s;
  `/tmp/xiuxian-r116-full-20260914.xml`. No failures, errors or skips in the
  focused, associated or full runs. All test sessions completed.

The intermediate fixed run's ten remaining failures were test expectations
about guard persistence: reloading before the existing dirty-state save may
restore a closed guard. The tests now separately verify in-memory idempotence
and exact reconciliation after that reload, without claiming an atomic guard
transaction. The subsequent boundary run reproduced the five real manual
admission failures described above. Native receipt-capacity overflow is also
covered: an unstored reply cannot complete a query, but read-only timeout can
retire its exact pending while retaining the capacity gap.

Candidate HEAD remains `cbf152cd61325ca9218c74ee995d643038b20905`.
Inherited worktree edits remain intact, including the user-owned quiz bank and
UI-key inspection tool. This checkpoint changes no shared send/guard
implementation and does not implement a new accounting controller.

## Remaining Gates

This is not a policy to auto-resolve retained resource holds. R65 legacy/no-ID
migration, unproved consuming intents, complete forced-stop/storage recovery,
operational capacity and beneficiary churn remain open. At hard capacity, a
read may still be refused rather than discard evidence. Shared R07 and
whole-project Final Review remain open.

Production code/state/config/services, listeners, skills and remotes are not
changed. No live game request, deployment, restart, commit or push is allowed
in this checkpoint. World Boss/refinement controls remain untouched; inventory
API remains UI-only; CommandAttempt remains shadow-only; deep retreat never
gates Tianxing. R67 subordinate-role MiniApp cultivation is still unvalidated
in production.
