# R92 Concubine Divination Lifecycle

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production files, services, settings, database,
live requests, listeners, skill changes, commits, pushes or deployments.
World Boss/refinement stay disabled; inventory API remains explicit UI-only;
CommandAttempt stays shadow-only. Deep retreat never consumes or blocks
Tianxing effects. Whole-project Final Review remains open.

## Finding And Reproduction

Divination had no durable intent before an untracked send. Unknown transport
was converted to a retry or a fabricated business cooldown, scalar anchors
authorized native/passive completion, and a log scan could project an unowned
success. Late transport and old replies could overwrite replacement work.

The first 22-case reproducer includes both missing-contract assertions and
unsafe scalar/native/passive acceptance; it is not 22 independent observed
production incidents. Artifact:
/tmp/xiuxian-r92-divination-reproduced-20260911.xml.

Final review additionally reproduced seven failing cases with 188 controls:
foreign named voyage refusals, boolean current snapshots, absent/non-future
unsent backoff, and multiline partner/effect names. These now fail closed.
/tmp/xiuxian-r92-review-reproduced-20260912.xml.

## Candidate Repair

- Add one bounded concubine_tianji_action JSON slot, registered as runtime/JSON
  state in both SQLite schema declarations. Capture identity, account, chat,
  unique operation, partner, affinity, snapshot, effect and plan before dispatch.
- Track a zero-retry send. Recheck ownership, switches, plan and sibling work
  immediately before dispatch; prevent queued affinity/fragment/voyage actions
  from crossing an unresolved divination. Only fresh definitely-unsent evidence
  allows local backoff and another attempt. Unknown/cancelled operations remain
  unresolved across restart; no timeout fabricates business CD or spending.
- Validate native/passive replies before generic dedupe. Require the original
  operation receipt, official sender, chat and server clock. A success needs
  complete partner/effect/cost fields. Explicit waits, voyage locks, affinity,
  resource and no-partner refusals are separate outcomes. Reject conflicts and
  wrong named partners. Missing affinity causes a read, not an invented zero.
- Preserve cultivation cost as evidence only; this change does not add another
  cultivation debit. Completed authoritative CD and unsent/resource backoff
  remain effective even if a scalar scheduling field is cleared.
- Preserve newer plans and snapshots, allow owned terminal facts after pause,
  clear only exact old pending rows, and release phases through R91's rule.
  Early completion survives late transport registration. Save/checkpoint
  failure rolls back; recovery replays exact operation logs with server time.
- Remove the unowned Tianji log guard, provisional-CD helper, old replay spec,
  old timeout-CD branch and unused terminal parser. Manual/legacy reconciliation
  is not replaced by a permissive fallback.

## Verification

- 142 additional cases: 125 divination lifecycle cases and 17 additions to the
  common phase/queued-sibling matrix. Covers native/passive routing, SQLite
  reload, actual runtime receipt/log creation, ambiguity, replacement owners,
  pause, cancellation, malformed state, save failure, refusal outcomes and CD.
- Focused final: 2069 passed, 24 subtests, 15.86s.
  /tmp/xiuxian-r92-focused-final-20260912.xml.
- Final isolated full: 10311 passed, 1275 subtests, 156.69s, exit code 0.
  /tmp/xiuxian-r92-full-final-20260912.xml.
- Configured/fatal Ruff, compileall, pip check and git diff --check pass.
  Tests use temporary SQLite and XIUXIAN_ALLOW_LIVE_TEST_DB=0; full suite has
  only loopback in an isolated network namespace. All sessions completed.

## Remaining Work

Heart and reacquisition still need their lifecycle review. Unowned legacy/manual
divination and other legacy phases require explicit evidence reconciliation.
R65/R74 accounting/capacity, shared R07 forced-stop durability, rollout/rollback
and whole-project Final Review stay open. The additive column is not a rollout
approval: old readers must not ignore unknown operations or completed cooldowns
and resume their timeout/reset behavior. Production jfdffdddd subordinate-role
MiniApp cultivation remains unverified; these tests do not prove it ran.
