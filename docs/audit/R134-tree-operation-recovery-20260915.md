# R134 Tree Operation Durability

Offline candidate only, in `/root/xiuxian-main-rebuild-20260907` on
`rebuild/stability-20260907`. This closes the current-operation durability
sub-scope of C3/C4, not either package or whole-project acceptance.

## Gap And Candidate Contract

R133 validates current receipts but does not record intent before dispatch.
A process killed between allocation, submit and final publication could lose
the pending operation or an already-returned result.

- Each identity now has one bounded `tree_operation` JSON column, at most
  256 KiB. Invalid or oversized data remains an explicit hold, not empty work.
  This is a current-operation slot, not a historical reward ledger.
- Checkpoints bind identity, account, authorization and the preceding MiniApp
  view. Entry/round identifiers are digests; raw tokens and init data are not
  persisted. Confirmed receipt prefixes and aggregate rewards are validated.
- The threaded worker requires a main-loop durable intent acknowledgement
  before `run_start` or `run_submit`. A timed-out queued callback cannot write
  later; an already-started synchronous save is drained for its acknowledgement.
- Proven pre-transport failures resolve as not sent. Ambiguous transport
  outcomes retain the original pending descriptor; an allocated round remains
  held until its settlement is confirmed. Returned settlements are checkpointed
  before subsequent reads or mutations.
- The final journal publication marker and owned MiniApp view are saved
  together. Failed publication rolls back that view and retains journal
  evidence for local recovery. Other identities' records are not pruned by
  this transaction.
- Local recovery makes no HTTP requests and publishes once. Unknown/allocated
  rounds remain held. Safely closed interrupted work is marked `interrupted`,
  so a later ordinary flow can read authoritative quota before further play.
- Replaced/deleted/rebound owners, changed MiniApp baselines and active writers
  cannot be overwritten by recovery. Direct, command, public and scheduled
  callers observe the hold; a held role does not starve later identities.

Proof generation, score policy, limits, World Boss/refinement switches and the
Tianxing/deep-retreat relationship are unchanged. CommandAttempt remains
shadow-only. No new shared recovery controller or game endpoint is introduced.

## Verification

- Expanded focused regression: 608 passed in 37.09 seconds.
  `/tmp/xiuxian-r134-tree-checkpoints-focused-20260915.xml`.
- Seven real SIGKILL/reload boundaries: intent, start dispatch, allocation,
  submit dispatch, settlement, second intent and completion. Reload retains
  exact pending work and confirmed reward prefixes without transport replay.
- Save rejection/exception, unknown transport, proven unsent budget exhaustion,
  native manual publication, owner replacement, invalid codecs and local-only
  once-only publication are covered.
- First full regression: one failure, 14427 passes and 1275 subtests. The old
  stale-entry test mocked eligibility without registering an identity. Adding
  its missing owner preserved all original timeout/no-resend assertions; no
  production guard was weakened. Failed artifact:
  `/tmp/xiuxian-r134-tree-checkpoints-first-full-20260915.xml`.
- Final full regression: 14428 passed and 1275 subtests passed in 406.60 seconds.
  `/tmp/xiuxian-r134-tree-checkpoints-full-20260915.xml`.
- Parsed JUnit comparison with R133: 28 additions (21 operation and seven
  process-restart cases), zero removals, zero failures/errors/skips, and every
  focused case present in the full run. Configured Ruff, scoped full-F,
  compileall, dependency and whitespace checks pass.
- Tests used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`, temporary state and private network
  namespaces; only loopback was enabled for full-suite local HTTP tests.

## Remaining Limits

An accepted server action whose receipt was never durably recorded remains
unknown. There is no authoritative original-round lookup/recovery or mutation
replay in this change. Holding it prevents repetition but does not finish it.
Historical/day-wide accounting, cross-day correlation, legacy reconciliation
and broader retention remain open.

Future rollout/rollback must preserve the journal and its linked MiniApp view;
an older reader cannot safely ignore an unresolved operation. Production was
not changed, migrated, restarted or probed. There was no commit/push, listener
operation or skill edit. Inherited quiz-bank and UI-key-tool hashes and HEAD
remain unchanged. R67 still lacks production cultivation verification for the
19 affected subordinate roles; whole-project Final Review remains incomplete.
