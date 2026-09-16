# R113 Yinluo Send Finalization

Status: scoped offline candidate verified; not production acceptance.
Parent: `rebuild-stability-20260907.md`, R65.

## Reproduced Failures

- The actual shared sender records `send_cancelled_unsent` when cancellation
  occurs before RPC dispatch. The Yinluo caller ignored that evidence and
  retained an unknown resource reservation indefinitely. Both manual Yinluo
  and resource-consuming Wanxin assistance reproduce this boundary.
- A native result can commit while a recovered transport caller awaits audit
  completion. Its already-completed operation rejected the identical return
  receipt and the manual endpoint reported `receipt_unverified`.
- Subsequent native coverage can retire that completed operation before its
  caller returns. Looking only in the hot operation list also misreported a
  valid, archived completion as a failed send.
- A previous same-clock unsent marker could release a later unknown attempt.
  A newly encountered marker with inconsistent identity metadata also passed.
- Repeated transport receipts could replace the original send time. Even an
  unchanged, already-saved sent receipt required another state transaction.

These are offline reproductions, not claims that these exact interleavings
occurred in production. The pre-dispatch cancellation test runs the real
shared send/cancellation branch with suspended admission and asserts zero
RPC dispatches. It does not contact Telegram or change the shared sender.

## Required Contract

1. Before invoking the sender, retain its existing block descriptor. A release
   needs a different, current, explicitly unsent descriptor for exactly this
   identity and command. Missing, inherited, old, future or mismatched evidence
   remains unknown. Cancellation or exception alone never proves non-send.
2. Existing operation-bound transport evidence takes precedence over a missing
   return object. Adopt only the existing exact pending receipt. A sent receipt
   still reserves resources; it is not business completion or permission to
   send again. A native terminal result owns completion.
3. A repeated receipt must keep the original operation UUID, account, chat,
   message ID and send time. An exact already-completed receipt is idempotent;
   it neither reopens pending work nor changes cooldowns or balances.
4. A return receipt may locate its exact completed cold operation. Validate
   the existing archive payload, digest and result index. Do not restore its
   financial history, scan every command, use a username to change ownership,
   or allow a receipt-less lookup to locate arbitrary old work.
5. An unchanged persisted receipt needs no new transaction. A genuinely new
   release or binding must still commit. If SQLite rejects the release, retain
   the original reservation in memory and after reload. Never report unsent
   merely because the staged save was attempted.
6. After replacement, rebinding or deletion, the old caller cannot write to
   the current identity. Module disable does not discard valid financial or
   transport facts and cannot be undone by cancellation cleanup.

The implementation is local to `yinluo_accounting` and the Yinluo send/manual
caller shared by financial Wanxin assistance. It adds no state field, SQLite
column, retry controller, new action, automatic switch or shared runtime edit.
Two old test fixtures were aligned with the boundary: an unsent marker is now
emitted during sending, and detached receipt adoption is tested both during
unwinding and on the following native event.

## Capacity Review

`MAX_BUSINESS_POINTS = 120` bounds business-clock **keys**, not operations.
The actual writers use action, slot, summary, shortage and beneficiary/action
keys. Repeated work for the same action/slot/beneficiary replaces those clocks;
it does not add a key for each execution. No per-120-operations defect was
established, so this change introduces no speculative business-clock eviction.
Growth from beneficiary/slot churn and operational cold-storage capacity still
need the separate R65 retention/migration policy.

## Verification And Limits

Initial four lifecycle reproducers failed. Follow-up tests reproduced four
ownership/archive failures, two send-time replacement failures and one
unnecessary-save failure. The tests also cover stale/mismatched block evidence,
real SQLite rejection and reload, return-less retained receipts, no-repeat
admission, disable/rebind/delete/replacement and corrupt/foreign archives.
The no-op save check initially compared the cultivation ledger with its storage
envelope; the regression remained red and that comparison was corrected before
the final run. An early full run was terminated and is not acceptance evidence.

- New lifecycle file: 48 passed, 4.49s;
  `/tmp/xiuxian-r113-focused-20260914.xml`.
- One additional existing detached-receipt case covers unwind-time adoption:
  49 total added cases relative to the R112 checkpoint.
- Final associated suite: 893 passed, 262 subtests, 54.96s;
  `/tmp/xiuxian-r113-related-final-20260914.xml`.
- Final network-isolated full suite: 12207 passed, 1275 subtests, 190.48s;
  `/tmp/xiuxian-r113-full-20260914.xml`. No failures, errors or skips.
- Configured repository Ruff and scoped F841/F401/F811/F821/F822/F823,
  compilation, dependency consistency and `git diff --check` pass. This does
  not assert that optional repository-wide F841 is clean.

All test sessions completed. Candidate HEAD remains
`cbf152cd61325ca9218c74ee995d643038b20905`; inherited worktree edits are preserved.

This is not an implementation of R07's separately gated shared transport
architecture. A process killed before a definitive unsent descriptor is
observed still retains prepared/unknown work. A timer, a fresh balance, or an
unrelated reply cannot release that reservation. Unowned legacy operations,
complete forced-stop migration, other resource consumers, storage operations
and whole-project Final Review remain open.

No production code, state, switch, service, listener, skill or remote repository
was changed. No live game request, deployment, restart, commit or push occurred.
World Boss and incense refinement remain disabled; CommandAttempt remains
shadow-only; inventory API reads remain UI-only. R67 subordinate-role MiniApp
cultivation still requires separately authorized production validation.
