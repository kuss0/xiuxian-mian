# R115 Yinluo Completion Cleanup

Status: scoped offline candidate verified; not production acceptance.
Parent: `rebuild-stability-20260907.md`, R65 and R114.

## Reproduced Failures

Thirteen initial cases failed. The native result transaction saved a completed
resource operation while its shared pending row was still present. Subsequent
cleanup removed that row only from memory. Reload restored the pending row;
without the old log, local recovery did not clear it even though the completed
native evidence remained in the hot book or cold archive. A manual query could
reach shared admission with this obsolete row, and a consuming timeout could
report completion while retaining its unresolved pending row.

Further review reproduced duplicate same-operation pending rows being only
partly cleaned, scheduler cleanup being skipped until the next business action,
and guard recovery failing to find a cold completed result. Existing hot guard
cleanup also ignored contradictory account/command/send-time metadata and
accepted coerced message IDs. One final candidate test found that a missing
guard timestamp bypassed the optional send-time check; it is now rejected.

These are offline reproductions, not claims of observed production incidents.
Two old delayed-finalizer cases were corrected to use the same original send
time for binding and finalization. Deliberately different clocks are now a
negative test. Two new reload assertions explicitly account for the normal
empty `chain_id` and `delete_policy` defaults, without weakening ownership
comparisons before cleanup.

## Required Contract

1. Native resource projection, operation completion, provider/beneficiary
   observations and removal of provably completed shared pending rows use the
   same state transaction. No result may be saved with cleanup deferred only
   to a subsequent in-memory deletion. Failed or exceptional saves restore
   the removed rows and staged accounting together.
2. Expired-read and completed-operation cleanup share the existing exact
   operation receipt validator. UUID, account/identity, source module, command,
   chat/message and original send time must agree. Duplicate or contradictory
   same-chat receipts stay intact; another chat's same-numbered row and a newer
   unknown operation remain untouched. Missing evidence is not an empty queue.
3. Legacy/manual rows without a script operation require their verified native
   command interval and valid transport metadata. A missing known script UUID
   binding is not reclassified as a manual operation to clear a hold.
4. Completion proof can come from validated hot facts, exact indexed cold
   evidence, or a validated retirement staged in this same transaction. Cold
   digest/payload/index ownership and archive revision checks remain mandatory.
   Retirement and pending cleanup either both commit or both roll back.
5. Existing completed pending rows reconcile from local durable facts even
   when the bounded log replay returns nothing. No Telegram request, command
   resend, balance reset, invented outcome or new controller is involved.
   No-op recovery needs no save. Preparation also removes already-completed
   rows in its normal transaction before the next shared send admission.
6. Enabled scheduler passes perform this local cleanup before business due-time
   checks, while still skipping active callers. Completion cleanup does not
   advance cooldowns, enable actions or alter the resource observation.
7. Guards are reconciled from their own exact current account/chat/message,
   canonical command and original send time, against hot or cold completion.
   They are not found by scanning only hot receipt families. Missing/malformed
   clocks and mismatched guards remain untouched; real remote cooldowns survive
   closure through the existing action-guard helper.

Guard metadata continues to use the existing helper's dirty-state persistence.
Its authority is the durable native completion: a restored guard can be
reconciled again before module work, without replaying a business action.
The result/pending transaction is atomic; this change does not claim a new
shared guard transaction or solve every forced-stop boundary.

## Scope And Verification

The implementation is confined to `model/yinluo_accounting.py` and
`model/features/yinluo.py`, with tests and audit documentation. The old direct
pending deletion was removed. No persisted field, schema migration, resource
price, retry policy, shared sender or action-guard implementation was changed.

- Initial completion/reload reproducers: 13 failed;
  `/tmp/xiuxian-r115-initial-20260914.xml`.
- Duplicate pending review: two semantic failures before correction;
  `/tmp/xiuxian-r115-boundaries-20260914.xml` also contains the two reload-default
  fixture failures described above.
- Scheduler/guard reproducers: seven failed before correction;
  `/tmp/xiuxian-r115-guard-initial-20260914.xml`.
- Missing guard clock reproducer: one failed before correction;
  `/tmp/xiuxian-r115-final-boundaries-20260914.xml`.
- Final new lifecycle file: 55 passed, 6.19s;
  `/tmp/xiuxian-r115-focused-final-20260914.xml`.
- An intermediate Yinluo suite passed 386 cases before the final ten boundary
  cases; it does not replace final associated/full regression.
- Configured repository Ruff, scoped F401/F811/F821/F822/F823/F841, compilation,
  dependency consistency and whitespace checks pass.
- Final associated suite: 1102 passed, 262 subtests, 66.42s;
  `/tmp/xiuxian-r115-related-final-20260914.xml`.
- Final network-isolated full suite: 12324 passed, 1275 subtests, 204.54s;
  `/tmp/xiuxian-r115-full-20260914.xml`. No failures, errors or skips.

All test sessions completed. Candidate HEAD remains
`cbf152cd61325ca9218c74ee995d643038b20905`; inherited worktree edits are
preserved, including the user-owned quiz bank and UI-key inspection tool.

## Remaining Gates

R65 legacy/no-ID migration, unproved consuming intents, complete forced-stop
and storage recovery, cold-storage capacity/monitoring and beneficiary churn
remain open. Existing legacy/receipt/capacity holds and book gaps remain
barriers; this change neither clears nor reinterprets them. Read-only
calibration/liveness under retained holds belongs to the remaining R65 review.
Tests exercise real temporary SQLite rejection, archive rollback
and reload; they do not claim complete process-interruption coverage. Neither
R07's separately gated transport architecture nor whole-project Final Review
is complete.

Production code/state/config/services, user controls, listeners, skills and
remotes were not changed. No live game request, deployment, restart, commit or
push occurred. World Boss/refinement controls remain untouched; inventory API
remains UI-only; CommandAttempt remains shadow-only; deep retreat does not gate
Tianxing. R67 subordinate-role MiniApp cultivation is still unvalidated in
production.
