# R114 Yinluo Read Lifecycle

Status: scoped offline candidate verified; not production acceptance.
Parent: `rebuild-stability-20260907.md`, R65 and R113.

## Reproduced Failures

Seven initial cases reproduced read queries that expired in the resource book
but kept their detached shared pending row, including after reload. That row
blocked the next read at shared admission. Expiry used preparation time even
when a later send receipt existed, and an expired unbound read rejected its
original late receipt. Scheduler reentry during an active caller could issue
another query or overwrite the observation that guarded a consuming send.

Review of the first candidate patch found additional defects: preparation
could prune an expired operation whose pending cleanup had failed; ambiguous
or contradictory pending metadata was not fully checked; an identical bound
receipt could reopen an expired read; active expiry could still adopt and
change the sender's snapshot; unrelated/malformed RPC records could claim an
active operation. A malformed pending container also raised instead of
retaining the unresolved read. These were reproduced before correction.

These are offline fault replays, not evidence of these exact production
interleavings. Two initial post-patch failures were test expectations that
omitted the normal reload defaults for an unrelated pending row. The tests
now compare its complete restored value before and after cleanup.

## Required Contract

1. Only the read-only banner query may expire without a native result. Its
   deadline starts at the known original send time, or preparation time when
   no receipt exists. Expiry is neither business success nor a financial
   release. Consuming prepared/sent/unknown operations keep their reservations.
2. Expiry and removal of its exact shared pending row commit together. Match
   operation UUID, source module, command, chat/message, original send time,
   account and any supplied identity metadata. Accept one exact legacy integer
   key. Preserve another chat's same-numbered message and reject duplicate or
   conflicting same-chat receipts. Malformed evidence is never empty evidence.
3. Failed or exceptional saves restore the staged accounting, cultivation,
   profile projection and removed pending rows. Reload must retain the original
   work. An already-expired record can finish its pending cleanup after reload;
   preparation cannot prune that record while cleanup is unresolved.
4. An expired unbound read may adopt its exact late original transport receipt.
   It then waits from the actual receipt time. Replaying an already-bound
   expired receipt does not reopen it, change its clock or require a new save.
   Native replies can still complete the old query without completing a newer
   query or removing the newer pending row.
5. Active callers are bound to the original identity object/account. Read the
   existing registered RPC handles as well, matching their current operation,
   actor/account, command, module and chat. A done task still awaiting its
   receipt callback remains active. Do not expire, adopt or replan that work;
   release the local caller marker in `finally`. Replacement, rebinding and
   deletion cannot transfer it to another owner.
6. A staged commit rechecks the current owner and accounting/evidence before
   writing. Silence, a wrong-account receipt or a timer cannot override those
   checks. Unknown consuming actions remain retained for native reconciliation.

The change is local to `yinluo_accounting.py` and `features/yinluo.py`.
`ResourceUpdate.expired_reads` is transient transaction metadata, not a new
persisted field. The caller marker is local lifecycle protection, not a retry
controller. The shared sender, persisted schema, user switches, resource costs
and business policies are unchanged.

## Verification

- New read-lifecycle file: 62 cases covering expiry, detached admission,
  original clocks, exact late/duplicate receipts, SQLite rejection/reload,
  exception rollback, malformed/conflicting evidence, active callbacks,
  scheduler reentry, cancellation and identity changes.
- Initial reproducers: `/tmp/xiuxian-r114-initial-20260914.xml`.
- Candidate boundary reproducers: 14 failures before correction;
  `/tmp/xiuxian-r114-boundaries-20260914.xml`.
- Malformed pending-container reproducers: two failures before correction;
  `/tmp/xiuxian-r114-owner-boundaries-20260914.xml`.
- Final focused suite: 256 passed, 48.41s;
  `/tmp/xiuxian-r114-focused-final-20260914.xml`.
- Final associated suite: 955 passed, 262 subtests, 58.98s;
  `/tmp/xiuxian-r114-related-20260914.xml`.
- Final network-isolated full suite: 12269 passed, 1275 subtests, 196.62s;
  `/tmp/xiuxian-r114-full-20260914.xml`. No failures, errors or skips.
- Configured Ruff, scoped F401/F811/F821/F822/F823/F841, compilation and
  dependency consistency and `git diff --check` pass.

All test sessions completed. Candidate HEAD remains
`cbf152cd61325ca9218c74ee995d643038b20905`; inherited worktree changes are
preserved. These results validate R114's current scoped candidate, not the
remaining R65 gates or whole-project acceptance.

## Remaining Gates

R65 legacy pending migration, complete forced-stop/storage recovery,
beneficiary churn/cold-storage capacity and whole-project Final Review remain
open. A completed resource result and generic pending cleanup still need their
own crash-boundary review; read expiry does not claim to solve every shared
pending lifecycle. No unproved consuming operation is discarded for capacity.

No production edit, state/config mutation, live request, service/listener
action, deployment, skill edit, commit or push occurred. World Boss and incense
refinement controls are untouched, inventory API remains UI-only,
CommandAttempt remains shadow-only, and deep retreat is not a Tianxing gate.
R67 subordinate-role MiniApp cultivation remains unvalidated in production.
