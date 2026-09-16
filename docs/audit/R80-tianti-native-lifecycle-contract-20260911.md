# R80 Native Tianti Lifecycle

## Scope And Authority

- Candidate: /root/xiuxian-main-rebuild-20260907,
  branch rebuild/stability-20260907, base cbf152cd.
- Offline code, replay and isolated SQLite tests only. No production changes,
  restart, live requests, listener activation, commit/push or skill edits.
- No shared CommandAttempt controller or retry ownership was introduced.
  CommandAttempt remains shadow-only; R67 rollout remains unapproved.
- World Boss and incense refinement remain disabled. Inventory API remains
  explicit UI-only. Deep retreat does not block or consume Tianxing effects.

## Confirmed Findings

The initial lifecycle reproducer returned **67 failed, 1 passed**:

1. Scheduler recovery awaits could outlive an identity, account, switch or plan
   and still dispatch the old command.
2. Wenxin, Gangfeng and status requests used transport retries. Missing RPC
   receipts, exceptions and cancellation did not preserve uncertain spending.
3. Delayed send receipts replaced newer results or wrote into replacement
   identity state. Profile rank changes were not part of queue admission.
4. There was no shared per-role exclusion for native scheduler/UI sends.
   Status replies could start a detached climb beside normal scheduling.
5. The passive reducer independently advanced timers and added rewards;
   native replies could save again after awaiting an audit notification.
6. Recovery inferred the current chat from scalar IDs, accepted broad bot
   markers and could substitute processing time for server evidence.

Further review reproduced a recovery liveness failure using an actual-format
JSON message log: a later manual command with the same text hid the original
owned send when the old helper returned only the newest command. The repair
searches by operation ownership and rejects conflicting roots.

## Candidate Contract

### Command Ownership

Each identity has at most four tianti_commands records: status, wenxin,
gangfeng and climb. The additive JSON column exists in both schema creation
and migration. Each record binds:

- identity ID, registered account ID and original chat;
- command, unique operation ID and dispatch/receipt times;
- rank policy captured at admission;
- lifecycle status, reply time and bounded replay scheduling.

The identity object, account binding, global/identity/module controls,
subfeature controls, selected public status path, active chat, profile rank
and business plan are checked across relevant awaits. Runtime queue admission
uses operation_check. Every native send uses max_retry=0 and the manifest
module name through TIANTI_SOURCE_MODULE.

| State | Meaning | Automatic replay permission |
| --- | --- | --- |
| sending | Intent saved before dispatch | None |
| sent | Scoped RPC or sent-log receipt known | Reply recovery only |
| unknown | RPC result missing, cancelled, failed or malformed | Reply recovery only |
| unsent | Fresh definitely-unsent evidence for this dispatch | Backoff then re-evaluate |
| complete | Owned, recognized terminal game reply applied | Normal next due cycle |
| expired | Obsolete/read-only status request | A later bounded read may run |

Only status reads expire by age. Mutations do not become retryable at day
change or when a timer elapses. Cached send-block evidence, including a cached
block at the same timestamp, cannot authorize a new retry.

Confirmed receipts survive switch-off without overwriting a changed plan.
Replacement identity/account owners are not written through. A completed
early reply wins over a delayed RPC return or exception. Intent persistence
failure prevents dispatch.

### Business And Replies

- Wenxin final-stage/last-climb/day-end policy and Gangfeng's ten-minute
  pre-climb window are preserved.
- The detached post-status climb and its time-only reservation are removed.
  Status replies calibrate state; the normal scheduler owns continuation.
- Active and passive delivery use one native handler. It requires the original
  chat/root, registered account ownership, known game-bot sender and valid
  server timestamp. Pause does not discard already-sent results.
- Complete status panels use the R79 parser. Partial/contradictory panels do
  not certify cached fields, close the operation or rearm timers.
- Resource denials and positive results cannot be mixed into one terminal
  transition. Costs, gains, progress ranges and explicit readiness/countdowns
  remain validated separately.
- Business state, command completion and exact pending cleanup are saved
  together before notification. Audit failure cannot undo a saved result.
- Cleanup is exact-chat/root/family, not "clear every Gangfeng command after
  any climb". A result for another command does not prove its outcome.
- Duplicate completed deliveries do not add Wenxin's extra levels twice or
  extend cooldowns. Server evidence, not replay processing time, anchors CD.
  Both the displayed CD and the next execution clock use that same timestamp.
- A strictly owned new manual result may calibrate idle work and supersede an
  older read. It cannot erase an unresolved mutation. Later stale reads do not
  replace that manual result.

### Recovery And Legacy Data

- Receipt adoption uses scoped runtime pending evidence or a unique sent-log
  match by account, identity, chat, operation ID, command and source module.
  "Latest message with the same text" is not sufficient.
- Reply searches inspect the original command's bounded window and a recent
  window, at most two windows per due operation. Replay is throttled to five
  minutes. No live Telegram scan occurs in the send path.
- Old pending records migrate only with a unique chat/root and explicit
  matching account evidence, either in the pending record or the exact sent-log
  row. The current account is not used to invent missing provenance.
- Historical tianti_last_*_msg_id scalars are also completed-result anchors.
  Nonzero scalars alone neither create an unresolved record nor freeze a role.
- Unowned or conflicting legacy pending work stays visible and blocked. It
  requires evidence-based reconciliation before deployment.
- MiniApp read admission recognizes the persisted mutation hold even after
  the old in-memory reservation would have expired.

## Verification

- Baseline: /tmp/xiuxian-r80-tianti-before-20260911.xml.
- First full suite: **8569 passed, 1275 subtests**, 143.78s;
  /tmp/xiuxian-r80-tianti-full-20260911.xml.
- Intermediate full suite after timestamp/manifest review:
  **8578 passed, 1275 subtests**, 144.14s;
  /tmp/xiuxian-r80-tianti-final-full-20260911.xml.
- Full suite after owned-log replay repair: **8580 passed, 1275 subtests**,
  144.31s; /tmp/xiuxian-r80-tianti-owned-replay-final-full-20260911.xml.
- Seven further failing cases exposed CD display drift relative to the
  server-anchored timer. All were repaired:
  /tmp/xiuxian-r80-tianti-display-before-20260911.xml.
- Final focused native/UI/background/read tests: **596 passed**, 3.56s;
  /tmp/xiuxian-r80-tianti-final-focused-20260911.xml.
- The later-manual-log regression failed before the operation-filtered repair
  and passed afterward. Conflicting roots for one operation remain unresolved.
- Final full suite after the CD display repair: **8587 passed, 1275 subtests**,
  144.66s; /tmp/xiuxian-r80-tianti-closeout-full-20260911.xml. This includes
  **284 new R80 tests** beyond the R79 baseline.
- Broad Ruff, compileall, pip check and diff checks pass. All test sessions
  have completed. The post-suite source change only corrects an obsolete
  docstring about the removed detached status continuation.

Tests include real result fixtures, real runtime receipt finalization,
active/passive/UI entrypoints, later calendar cycles, cancellation and
exceptions, scoped migration, original-chat recovery, wrong bot/owner/root
negatives, manual coexistence, partial edits and SQLite reload.

## Limits And Remaining Work

- This is not production validation. jfdffdddd's subordinate-role cultivation
  remains unverified; candidate R67 playerId fixes do not prove live resumption.
- Post-terminal changed/corrected result edits are not reapplied automatically.
  The terminal record is idempotent; a later correction must not reapply an old
  cooldown or overwrite a newer operation. Correction accounting/field-level
  chronology beyond this contract needs separate review.
- Evidence absent from both bounded log windows remains unknown. Missing
  logs are not proof of failure, completion or permission to resend.
- Legacy rank policy is read at strict migration; historical rank changes are
  not reconstructed. Reconcile legacy records before any production rollout.
- Four latest records bound hot-state growth, not full historical accounting.
  An unresolved mutation is retained indefinitely until owned evidence closes
  it. No open-business archive/controller was introduced.
- R65/R74 capacity and legacy migration, concubine optional-field/native
  lifecycle, R07 shared crash durability and whole-project Final Review remain
  open.
- Rollback is not code-only: old code that ignores tianti_commands can resend
  uncertain work. A future deployment requires snapshot, reconciliation and
  an explicitly reviewed migration/rollback procedure.
