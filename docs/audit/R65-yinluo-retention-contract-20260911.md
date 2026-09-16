# R65 Operational Retention Contract

Status: offline candidate integrated; final regression evidence is recorded in
the parent audit. This is not deployment approval or approval of R07's shared
transport/controller work. Remaining acceptance gates below stay open.

## Required Boundary

- The hot resource book retains at most 256 native receipts and 64 operations.
  Completed work must not permanently occupy dispatch slots.
- A consuming command may leave the hot book only when its terminal outcome is proved,
  no live operation can own it, and every affected concrete resource is covered
  by a nonconflicting, later native balance. A balance is not an outcome.
- Unknown soul names, unresolved commands, conflicting ownership, unverified
  balances and uncovered shared cultivation entries cannot be discarded.
- Compaction must preserve balances and observed business clocks exactly. It
  cannot remove foreign cultivation facts or introduce a synthetic baseline.
- New mutations leave one operation slot for a read-only native banner query.
  A verified incoming panel may supply coverage before compaction, but its
  receipt, balance and retirement must commit together. Already-missing facts
  and old full/uncovered operation sets are not silently repaired by this rule.
- R119 also reserves reply capacity and archives exact expired read evidence.
  Query expiry never proves a mutation outcome or removes a balance conflict.
- R120 retains cold native boundaries for original bound, completed assistance
  points when the 120-entry hot business map is full. Distinct beneficiaries
  must not exhaust that map indefinitely; unproved manual/fixed points cannot
  be evicted merely to regain room.

## Archive

Use an identity/account/chat/original-command key in the existing SQLite
database. Store the original native receipts and the completed operation,
including its beneficiary identity/account and commission/publication/acceptance
binding. Index native result messages separately so a contextless edit can find
its exact old owner without scanning all identities or histories.

R119 adds cold payload v2 only for exact expired native banner reads. It
preserves the original read operation and all received revisions, including a
formerly useful panel later withdrawn. Such records have no financial effects
or requirements; their unknown/conflicting outcome is not completion. Existing
terminal financial payload v1 is unchanged. Expired no-reply metadata follows
R114, while expired received evidence is not silently deleted to free space.

R120 adds `yinluo_archive_business`, keyed by provider identity/account and
`assist:<beneficiary>:banner|strip`, plus an expected count/digest manifest in
the hot accounting JSON. Retired points keep their exact native intervals and
original operation/beneficiary proof. Missing/replaced cold rows cannot appear
as a never-observed key. A genuinely old schema only gains an empty manifest;
an existing nonempty index requires its original expected manifest, not a new
one inferred from surviving rows. Preserve both in migration/rollback.

Archive insertion/removal and both hot resource projections belong to the same
normal state transaction. A compare-and-swap digest prevents stale archive
updates. An archive write failure or conflicting revision rolls back the entire
staged update; an in-memory delete before a successful commit is not retirement.
R120 also requires exact pairing of both sides of each business-point move,
native-proof revalidation under the SQLite writer lock and a post-write
manifest check. The manifest scans provider-scoped key/digest metadata, not all
native payloads, but its cost still grows with cold-key count.

The archive is cold audit evidence, not a second resource balance or send queue.
Read only the matching command/result. Do not load its complete history on every
balance read. A completed operation can be restored for a later correction,
with the same original beneficiary binding and source ownership.
Restored hot roots are persisted and must refer to retained hot receipts. This
bounded marker preserves the absence of an original manual-assistance binding,
including through a second correction after SQLite reload. Restoring a record
must not create a new beneficiary by looking up the current target username.

No wall-clock TTL is asserted for Telegram edits. Consequently cold financial
evidence is not automatically expired: deleting it would invalidate exact
late-edit handling. Its disk growth must be measured and reported separately
from the bounded hot book. This policy does not claim infinite disk capacity or
permit silent archive deletion; storage/export operations require their own
verified lifecycle. No retention change may bypass a persistence failure.

## Late Evidence

- A duplicate archived reply cannot charge again or clear a newer commission.
- An admitted correction restores only its original command, folds its native
  revisions once and preserves foreign resource entries. A correction newer
  than a balance remains uncertain until an adequate native baseline exists.
- A contextless edit can invalidate only an exactly indexed old result from
  the same official sender/chat; it cannot credit its unqualified new text.
- Changed provider accounts, changed beneficiary accounts, invalid archives
  and conflicting result ownership do not acquire current ownership by name.
- Only known-unsent operations and expired read-only queries may be dropped
  from dispatch without a business outcome. Received query evidence still
  requires the R119 cold ownership record. Compaction never resends a mutation.

## Verification

Exercise the real temporary-SQLite commit/reload and runtime reducer paths:
capacity rollover, explicit native coverage, unknown ownership, foreign
cultivation, duplicate and corrected archived results, contextless edits,
beneficiary rebinding, corrupt payloads, conflicting archive writes and SQL
failures between archive and hot-state writes. Measure a multi-cycle run beyond
the old limits. Passing pure projection tests alone is not acceptance.

Legacy capacity gaps already missing a receipt remain an explicit recovery
problem. A new balance alone must not clear such a gap. R65's other pending
migration, forced-stop and nonfinancial Wanxin gates remain open.

The R119 lifecycle contract covers useful native calibration before normal
capacity rollover, exact expired-read archival and late restoration. It does
not retire business points or resolve already-missing data. Its own report
records current regression evidence and the v2 rollback constraint.

R120 extends verification to distinct-beneficiary churn, old/new commissions,
hot/cold business boundaries, incomplete moves, malformed/missing manifests,
native-proof races during save and fact adoption under disabled controls. Its
78-case focused suite passes, including the default 120-point bound with 122
beneficiaries. Associated/full verification is recorded in the parent audit.
The final isolated full run passes 12812 cases and 1275 subtests after the
last staged-slot guard; it includes all associated modules.
This does not permit silent migration of unproved manual points or deletion of
cold history to cap disk usage.

## Candidate Evidence (2026-09-11)

- The original runtime reproduced a `capacity` refusal at the 65th completed
  assistance operation, even with newer native sha coverage after each result.
  Repeated native panels hit a receipt gap on the 256th total stored panel.
  Both tests use the real temporary-SQLite runtime reducer, not synthetic
  terminal phase assignments. They pass with retirement enabled.
- Separate 80-cycle stress uses six operation slots and 12 receipts to force
  repeated rollover. It finishes with two hot operations/four receipts and
  157 archived commands, 92,796 encoded payload bytes. These are fixture
  payload bytes, not total SQLite/WAL disk usage or production estimates.
  Metrics are included in the focused JUnit artifact.
- Repeated/late native results, contextless edits, preserved foreign
  cultivation, unknown outcomes and soul names, early unbound results, original
  beneficiary rebinding, corrupted payload/digest/index, orphan index rows,
  write-lock/CAS conflicts, SQL rollback and state reload are covered.
- Archive CAS obtains the SQLite writer lock before checking revisions. The
  normal state transaction owns both archive changes and hot projections; a
  failure after an archive insert/delete rolls back SQLite and the staged
  in-memory changes. This does not certify the entire send/forced-stop cycle.
- Integrity failures retain existing state and hold financial admission.
  They do not invent a financial delta, infer a new commission or re-send a
  command. Lost archive evidence still requires explicit recovery, not a
  silent reset. R65 is not closed by these component tests.
- R74 follow-through also covers an already-accounted archived assistance reply
  whose original Wanxin waiting slot was restored. The exact stored operation
  and beneficiary binding can close that slot without loading cold history into
  the hot book, changing resource balances, or consuming a replacement
  commission. SQLite reload and unchanged archive statistics are asserted;
  this does not authorize clearing unmatched or unowned legacy waiting work.

Artifacts: `/tmp/xiuxian-r65-retention-focused-20260911.{xml,log}` and
`/tmp/xiuxian-r65-retention-full-20260911.{xml,log}`. Consult the parent audit
for the final rerun count; interim green counts do not certify later edits.
