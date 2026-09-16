# R124 Trial Operation Durability And Recovery

Status: scoped offline candidate repaired and verified. Associated regression
passes 2668 cases/210 subtests; the isolated full regression passes 13256
cases/1275 subtests. Production and whole-project Final Review remain open.

## Review Findings

- Trial workers returned only transient results. A process stop after `start`,
  `finish` or `next` lost ownership, the request stage and known settlements;
  another entry could then consume new work without reconciling the old one.
- An initial journal draft validated shapes and receipt prefixes but did not
  validate pending transitions. Completion could clear an unknown request,
  a foreign challenge could replace it, or another request could skip the
  current challenge. Malformed enum containers also raised instead of holding.
- Completed journals from another account/identity could pass manual admission.
  A save callback changing the owner or record could be overwritten by rollback
  or mistaken for this writer's successfully saved checkpoint.
- A queued checkpoint timeout and an already-running synchronous save were
  treated alike. Losing the latter acknowledgement could strand a proven-unsent
  intent. Retrying a completed pending save could open fresh HTTP in that same
  supposedly local-only recovery call.
- Manual UI recovery could close a still-running worker's settled checkpoint
  or authorize a second entry while the shared trial lock was held.
- Invalid/conflicting next-token aliases could fall back to the old token when
  a challenge was also present. The generic HTTP classifier also discards wait
  hints for 408/425; those still need retention as unknown trial requests.

## Required Contract

1. Persist an identity/account-owned intent before actual `start`, `finish` or
   `next` transport. A missing/failed persistence acknowledgement cannot dispatch.
   Budget/admission rejection before transport is not an unknown sent action.
2. Returned settlements acquire stable per-operation round identities.
   Successfully saved facts survive restart. A failed save retains facts in
   memory, stops further requests and leaves the last durable intent held;
   unsaved facts are not advertised as durable. No token, initData, URL, proof
   or raw HTTP envelope enters the journal.
3. Both public and authorized command entry paths check the journal before
   creating another trial entry. Unknown requests and unfinished challenges
   remain scoped to their original identity/account, not a global MiniApp hold.
4. An interrupted operation with no outstanding request can recover its known
   results locally, without game HTTP. Uncertain results cannot be resolved by
   retrying `finish`, reopening an entry or fabricating daily exhaustion.
5. The codec retains malformed/oversized records as an explicit hold. Checked
   saves, exact owner/revision checks and ordered receipts prevent stale writers
   or replaced identities from inheriting another operation.
6. Verify worker, real SQLite, both callers, UI admission, cancellation and
   process-kill/reload paths. Preserve successful solvers, requests budgets,
   existing controls, and independent non-trial gameplay.

## Candidate Repair

- Added one `trial_operation` runtime column per identity, independent of
  CommandAttempt. It retains owner/account/player, operation ID, ordered
  revision/checkpoint, hashed request and challenge identities, normalized
  current receipts and pending-save state. Limits are 99 receipts and 256 KiB
  per identity; an incomplete record is never discarded to admit another run.
- Worker checkpoints run on the owning event loop. An intent must be durably
  acknowledged with literal `True` after budget/admission checks and before
  transport. The transport guard runs again after acknowledgement. Queued
  callbacks can time out without later writing; an already-running synchronous
  save drains so its acknowledgement cannot be lost.
- Ordered transition checks bind `start -> challenge -> finish -> settlement`
  and `next -> entry/challenge`. A known challenge/entry remains unfinished
  without falsely claiming a sent action is unknown. Actual unresolved
  requests retain an unknown outcome. Completion alone cannot clear either.
  A saved pre-transport intent is conservatively unknown after a crash even
  when its aggregate `action_dispatched` field was still false at save time.
- Explicit proven-unsent or definitive application rejection evidence can
  restore only that request's exact prior stage. Matched settlement facts can
  close only their own finish intent. Existing receipts cannot be removed,
  changed, reordered or superseded by a stale callback.
- Saves check the same owner and exact record both before and after persistence.
  Failed ordinary saves restore only their own state; failed final saves keep
  returned facts in memory with `pending_save`, never overwriting a replacement
  record. Completion closes the writer to all subsequent callbacks.
- Public and command paths check local recovery before starting a worker; the
  public check precedes session/external-entry acquisition. Manual UI recovery
  and authorization additionally reject an active trial lock. Foreign completed
  journals are held, and local save retries cannot open new HTTP in that call.
- Next-token metadata is checked across the existing supported response
  containers. Absent metadata may accompany a same-entry challenge, but present
  invalid/conflicting aliases cannot silently reuse the old entry. 408/425 wait
  hints are retained in this journal without changing global classification,
  retry policy or permission to replay any mutation.

## Offline Evidence

- Initial worker/callback reproduction: 6 failures;
  `/tmp/xiuxian-r124-repro-20260914.xml`. The first worker checkpoint passed
  394 cases/14 subtests, and the first native/SQLite checkpoint passed 165 cases;
  `/tmp/xiuxian-r124-worker-first-20260914.xml` and
  `/tmp/xiuxian-r124-native-first-20260914.xml`.
- Seven real spawned-process SIGKILL/reload boundaries cover saved intent,
  dispatched start, known challenge, dispatched finish, saved settlement,
  dispatched next and returned next token. Only fixture child processes are
  killed. Reload performs zero game HTTP. The first combined restart suite
  passed 35 cases; `/tmp/xiuxian-r124-restart-first-20260914.xml`.
- Transition/admission/token follow-up: 29 failed, 34 passed before repair;
  `/tmp/xiuxian-r124-transition-repro-20260914.xml`. The corrected suite passed
  70 cases including the process-restart boundaries;
  `/tmp/xiuxian-r124-transition-fixed-20260914.xml`.
- Active-worker UI recovery reproduced 2 failures/43 passes;
  `/tmp/xiuxian-r124-admission-repro-20260914.xml`. Expanded queued/running
  timeout, per-stage failed-save, real SQLite abort, late-callback and falsey
  journal tests then passed 96 cases;
  `/tmp/xiuxian-r124-faults-first-20260914.xml`.
- Normal multiround entry variants, exact rejection/proven-unsent transitions,
  owner changes and native cancellation outcomes passed 207 cases;
  `/tmp/xiuxian-r124-lifecycle-final-20260914.xml`.
- Expanded HTTP fault coverage found 6 failures for lost 408/425 wait hints,
  with 2662 cases/210 subtests passing;
  `/tmp/xiuxian-r124-related-final-20260914.xml`. After the scoped retention fix,
  the associated suite passed 2668 cases/210 subtests in 62.48s;
  `/tmp/xiuxian-r124-related-verified-20260914.xml`. It includes trial, fishing,
  cave, persistence, MiniApp, WebApp and UI/control contracts.
- Full isolated regression: 13256 passed, 1275 subtests, 335.58s;
  `/tmp/xiuxian-r124-full-20260914.xml`. Parsed full/associated JUnit reports have
  zero failures, errors or skips and contain all 128 new R124 cases: 19 worker
  checkpoints, 102 operation tests and 7 process-restart cases. Two additional
  native cancellation cases extend R121 to 94 lifecycle cases; all 185 R122
  receipt and 37 R123 batch cases also remain green.
- Configured repository-wide/scoped Ruff, compileall, dependency and whitespace
  checks pass. All test sessions finished; execution used an isolated network
  namespace and temporary state, never the live database. Branch remains
  `rebuild/stability-20260907`, HEAD `cbf152cd61325ca9218c74ee995d643038b20905`.
  No commit/push occurred; inherited quiz-bank and UI-key-tool edits are intact.

## Evidence And Limits

The local implementation and read-only upstream references expose only
`start/finish/next`; no authoritative result-only recovery endpoint is available
in this evidence set. Automatic reconciliation of a genuinely unknown action,
reconstruction of a lost original entry, pre-worker external-entry allocation,
cross-day/history identity and durable notification delivery remain separate
requirements, not assumptions that a green mock test can establish.

The journal is the latest operation, not a cross-operation history/archive or
notification outbox. Recovering local facts does not prove inventory projection
or durable delivery. Pre-worker public external-entry allocation and queued
command-entry requests are not newly journaled by this patch. If every receipt
save fails and the process then dies, only the earlier durable unknown intent
is guaranteed to survive; recovery must not invent the missing receipt. A
synchronous save already running on the event loop cannot be independently
cancelled by the worker timeout. Those operational limits remain explicit.

No production/live game request, config/DB mutation, service/listener operation,
skill edit, World Boss/refinement change, deployment, commit or push. R67
subordinate-role cultivation has not been validated in production.
