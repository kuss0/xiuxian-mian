# R98 Wanxin Commission Reply Contract

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

Owned nonfinancial commission replies still used permissive text shortcuts:

- Publication without a valid ID completed the operation with ID zero.
  Partial, duplicate or contradictory publication facts replaced commission
  state. Acceptance titles did not require one complete helper/owner pair.
- Identification titles completed the action with missing gains defaulted to
  zero. Ambiguous targets, malformed quantities and instructional text passed.
  The named helper was not checked against the original command actor.
- Cancellation substrings overrode contradictory or uncertain text. A valid
  positive title could override an explicit non-execution suffix.
- Local-log reconstruction could use an incomplete latest publication or
  acceptance edit as proof that a commission was established.

Offline reproductions, not production incident counts:

- Initial: 56 failed, 4 passed.
  /tmp/xiuxian-r98-reproducer-20260912.xml
- Expanded refusal review: 5 failed, 74 passed.
  /tmp/xiuxian-r98-contract-review-20260912.xml

## Contract

- Publication and already-existing replies require one complete, positive,
  bounded ASCII integer commission ID. Missing facts do not default to zero.
- Acceptance requires the supported acknowledgement with exactly one helper
  and one owner; usernames are bounded ASCII tokens, not prefix matches.
- Identification requires the supported targeted acknowledgement and explicit
  source and contribution gains. Zero remains a valid explicit quantity.
  The contribution aliases share one uniqueness check. Absolute panel totals
  remain distinct from action deltas under the R97 contract.
- Native identification checks both the original helper and the owner against
  their existing identity/alias records. Renaming the same helper does not
  break the original actor binding; a different named helper is rejected.
- Conflicting kinds, incomplete facts, instructions, processing messages and
  explicit non-execution do not settle an operation. Identification cooldowns
  retain the action-specific R97 wait contract; positive facts plus a wait are
  ambiguous rather than successful.
- Supported cancellation acknowledgements and the real no-cancellable-task
  response remain terminal. Cancellation denial, expiration and missing-target
  responses need their own context and cannot borrow a success result.
- Unknown results retain module/shared pending ownership, do not spend daily
  slots or fabricate cooldowns, and cannot trigger a repeat on day rollover.
  Exact final edits still complete once, including after SQLite reload.
- Preserve R73/R74 native ownership, chronological rejection and atomic save
  rollback. A failed completion save leaves the owner and original actor's
  pending work intact. The latest malformed log revision invalidates that
  evidence, independent of log iteration order.
- Remove the old loose nonfinancial commission branches. Do not remove or
  replace the remaining financial compatibility parser: external commission
  replay still uses it, and native banner/strip accounting still delegates to
  R65 before the nonfinancial handler. No second resource writer is added.

## Verification

- 79 additional tests in tests/test_wanxin_commission_contract.py.
- Existing real publication, acceptance, identification, cancellation and
  financial replay fixtures remain unchanged and pass.
- Focused Wanxin/Yinluo/evidence/phase/UI regression: 1147 passed, 62 subtests;
  43.18 seconds. /tmp/xiuxian-r98-focused-20260912.xml
- Configured/fatal Ruff, scoped unused-symbol Ruff, compileall, dependency and
  whitespace checks passed.
- Final network-isolated full regression: 11137 passed, 1275 subtests;
  162.95 seconds. /tmp/xiuxian-r98-full-first-20260912.xml
  JUnit: 12412 cases including subtests, no failures/errors/skips.
  All test sessions completed. Only documentation changed after this run.

## Remaining Gates

This closes the reproduced nonfinancial commission text contract, not the whole
Wanxin module. R74 persisted sending after forced stop, unowned legacy/manual
operations, post-consumption corrections, cross-writer affinity chronology,
R65 retention/capacity and R07 shared durability remain separate review work.
Moon-join success still lacks an authoritative fixture. Production per-role
acceptance and whole-project Final Review remain open.
