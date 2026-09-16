# R99 Wanxin Restored Sending Contract

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production code, state, switches, services, game
requests, listeners, skills, commits, pushes or deployments are changed.
World Boss/refinement stay disabled, inventory API stays explicit UI-only,
CommandAttempt stays shadow-only, and deep retreat never affects Tianxing.
R67 production subordinate-role cultivation remains unvalidated.

## Reproduction

- A saved Wanxin operation in `sending` permanently blocked the module after
  its process disappeared. The reply deadline did not matter: it was always
  treated as a live sender, preventing independent configured actions too.
- Financial assistance could clear the owner's commission/held operation from
  matching command text and time while its provider operation still had no
  message ID. The provider retained its reservation, but the beneficiary had
  already advanced. This is not proof that the unknown send completed.
- A native result could remove a registered transport pending row before its
  operation adopted that exact receipt. A failed adoption could similarly be
  followed by terminal cleanup, losing the binding needed for later recovery.

Initial restart reproduction: 11 failed, 7 passed.
/tmp/xiuxian-r99-reproducer-20260912.xml
Unbound financial mirror review: two failed.
/tmp/xiuxian-r99-unbound-reproducer-20260912.xml
Follow-up review of old completed roots alongside new unknown work: two failed.
/tmp/xiuxian-r99-old-root-review-20260912.xml

## Contract

- Keep a process-local marker for the exact owner/action/operation while its
  sender is running. Both ordinary and financial Wanxin dispatch use it.
  Normal completion, non-send, exception and cancellation release only that
  marker in `finally`; an old caller cannot erase a replacement marker.
- After the reply deadline, a saved `sending` operation without its live marker
  may move into the existing fixed unresolved-action slot. Complete original
  owner/account/actor/chat/command ownership is still required. Normalization
  alone never changes it, and corrupt or unowned records are not repaired with
  today's account information.
- The move preserves all intent, original IDs/times, shared pending work and
  resource reservations. It means unknown, not sent, successful, failed or
  safe to retry. Existing commission and affinity dependency exclusions still
  apply; only independent configured actions can proceed through the existing
  serial send path. A failed save cannot release the hold or admit a new send.
- Disabled cleanup may retain/reconcile existing operations without enabling
  the module, identity or global control. Day rollover and SQLite reload do
  not erase uncertainty or rearm its mutation. A live sender remains protected
  through reentrant scheduler/cleanup calls, including after disable.
- A provider operation selected for a financial assistance reply must have the
  exact command message ID and a completed resource outcome before the owner
  projection can consume its commission or clear its matching held operation.
  Text/time resemblance of an unbound operation is only a reason to hold; it
  is not evidence to attribute a result to that operation.
- Native Yinluo processing may adopt an already registered exact shared receipt
  using the existing `adopt_pending_receipt` validator. Account, module,
  operation UUID, command and chat are still checked. Contradictory metadata
  and failed adoption preserve the pending evidence instead of running terminal
  cleanup. An already bound old root is independent of a newer unbound send.
- Binding an existing receipt is a transport-evidence save, not a resource
  debit. The subsequent native result retains the existing atomic provider/
  beneficiary projection. A crash between those saves leaves bound unresolved
  work that can be replayed; it cannot invent a terminal outcome.
- Early native resource facts can precede the send receipt, but do not complete
  an unbound owner mirror. Once the receipt exists, exact native replay can
  complete both sides once. The two existing early-reply fixtures keep their
  original game wording; the real-finalizer fixture still uses actual runtime
  registration. They now assert pre-receipt holding and post-binding replay
  instead of accepting premature owner completion.

No shared transport controller, retry, status-query loop, schema field or
runtime flag is introduced. The process marker is not a persistent lock.
The no-ID operation remains held when no exact receipt ever arrives. Remove
one unused Yinluo parser import; the active parser remains in the accounting
module and no public caller depended on the old import.

## Verification

- 39 additional cases: 26 module restart/liveness tests, two real spawned-
  process kill/reload cases, and 11 resource integration cases.
- Spawned tests kill only their isolated fake sender after the real module
  persists intent, then load the same temporary SQLite in a fresh process.
  Visit and publication each send once before the kill; after restart the
  original operation is held and only independent protection is dispatched.
  All worker processes are joined. No game request or real client is used.
- Final focused regression: 1186 passed, 62 subtests; 47.57 seconds.
  /tmp/xiuxian-r99-focused-final-20260912.xml
- Configured/fatal Ruff, scoped unused-symbol Ruff, compileall, dependency and
  whitespace checks pass.
- Final network-isolated full regression: 11176 passed, 1275 subtests;
  169.49 seconds. /tmp/xiuxian-r99-full-first-20260912.xml
  JUnit: 12451 cases including subtests, no failures/errors/skips.
  All test sessions completed. Only documentation changed after this run.

## Remaining Gates

This repairs scoped module liveness, not the R07 shared no-ID crash-durability
design. Unowned legacy/manual operations, missing evidence beyond retention,
post-consumption corrections, cross-writer affinity chronology and R65 capacity
remain separate work. Moon-join success still lacks an authoritative fixture.
Production per-role acceptance and whole-project Final Review remain open.
