# R132 Tree Current Quota Authority

Status: implemented and verified in the offline candidate. This is not
production validation or whole-project Final Review.

## Finding

The native tree worker treated field presence as quota authority and converted
counts through `int(float(...))`. Missing, fractional, negative, boolean,
non-finite and inconsistent counts could allocate another round or become
false exhaustion. Daily state discovery recursively searched `result`/`state`
subtrees; unrelated or historical panels could replace missing current data.
After a submit, one moving counter was sufficient to authorize further runs.
Missing sibling-mode state could instead silently skip that mode.

## Contract And Repair

- `used` and `limit` must be exact nonnegative safe integers. An explicit
  `remaining` must satisfy `used + remaining == limit`; it may be derived only
  when absent and both required counts are valid. Booleans, fractional values,
  coercion failures and out-of-range values are not zero. A consistent explicit
  zero limit is known exhaustion, not an invented default daily quota.
  An explicit non-true `ok` on the selected council, daily or mode panel
  invalidates its counts even when all numeric fields are present.
- Read current `council.daily` at the response root or one unambiguous `data`
  envelope. Only `run/submit` may use the native `seasonState.daily` snapshot.
  Do not recursively discover quota in `result`, arbitrary `state`, nested
  envelopes or historical panels. Ambiguous current panels require a read;
  they cannot authorize another mutation.
- A next daily round requires valid quotas for both modes, stable limits,
  nondecreasing usage and nonincreasing remaining counts. The submitted mode
  must advance in both consistent counters. External concurrent consumption
  may advance more than one use; it must not reset another mode's quota.
- Preserve supplied season/day context across continuation. Changed or missing
  previously established context does not authorize another run. Malformed
  declared context is distinct from absent legacy metadata. The worker does
  not invent a server date when none was supplied.
- Incomplete/invalid submit quota uses at most one current read within the
  existing operation budget. If that read establishes current complete state,
  continue the remaining modes; otherwise stop with `quota_unknown`. Already
  returned submissions and rewards remain retained. This is not mutation retry
  or a new cross-operation quota recovery engine.
- Existing proof/score policy, zero-score and verification-mismatch stops,
  operation ownership, cancellation draining and global request limits remain
  unchanged. Public MiniApp and group availability remain independent.

## Verification

- The original native worker reproducer had **97 failures, 14 passes**;
  `/tmp/xiuxian-r132-quota-repro-20260915.xml`. This exercises actual single and
  daily flow dispatch, not only parser helpers.
- Initial implementation plus existing worker/caller/protocol suites passed
  **363 cases** in **26.40s**;
  `/tmp/xiuxian-r132-native-first-20260915.xml`.
- Final review found that a malformed declared `season` container was still
  treated as absent legacy metadata. The reproducer had **8 failures, 8
  passes**, `/tmp/xiuxian-r132-context-repro-20260915.xml`; the container check
  is now strict. Additional tests cover required missing fields, explicit
  zero limits, native envelopes and concurrent external quota consumption.
- Native direct, command, public and queued scheduler paths additionally
  verify that malformed initial quota never opens a round, malformed later
  quota cannot mark completion, returned materials survive, and a missing
  sibling snapshot can be repaired by the existing current read.
- The pre-final focused/associated reports passed **667 / 4509 cases**, with
  **5 / 295 subtests**. Further review reproduced **10 failures** where an
  explicitly negative current panel still authorized counts;
  `/tmp/xiuxian-r132-negative-panel-repro-20260915.xml`. That failure flag now
  invalidates the selected panel, and all three panel depths are tested.
- Final focused verification: **693 passed + 5 subtests**, **27.97s**;
  `/tmp/xiuxian-r132-focused-accepted-20260915.xml`.
- Final associated verification, including all treasure suites:
  **4535 passed + 295 subtests**, **125.88s**;
  `/tmp/xiuxian-r132-related-accepted-20260915.xml`.
- Final full verification: **14341 passed + 1275 subtests**, **394.18s**;
  `/tmp/xiuxian-r132-full-accepted-20260915.xml`. All runs use temporary state
  with `XIUXIAN_ALLOW_LIVE_TEST_DB=0` inside `unshare --net`. Only namespace
  loopback was enabled for HTTP smoke tests; no game network was available.
- Parsed all final XML reports against the R131 full baseline: identical sets
  of **174 added cases** (162 quota cases and 12 additional native caller
  cases), no missing baseline cases, and no failures/errors/skips. JUnit totals
  include subtests, while the counts above list them separately.
- Configured Ruff, scoped full-F, compileall, dependencies and tracked/untracked
  whitespace checks pass. The seven full-F UI warnings remain identical to
  HEAD; that broader check is not green. Inherited quiz-bank/dump-tool hashes
  and HEAD `cbf152cd61325ca9218c74ee995d643038b20905` are unchanged. All test
  sessions have ended. Production remains unvalidated.

## Remaining Work And Boundary

This repair does not verify current tree settlement score/verification types,
run-token/mode/player binding, reward-container authority, historical reward
deduplication or inventory projection. The existing broad reward traversal
remains a separate review target; this quota repair must not be described as
complete receipt authority. Server chronology cannot be proven from absent
metadata. Durable per-request intent, original-round reconciliation, pre-memory
commit failure and crash-safe result accounting remain open.

No production edits/config/DB, game requests, deploy/restart, listener,
commit/push, skill or subagent changes. Preserve World Boss/refinement switches,
inventory API UI-only use, CommandAttempt shadow-only and Tianxing/deep-retreat
independence. R67 subordinate-role cultivation remains unvalidated in production.
