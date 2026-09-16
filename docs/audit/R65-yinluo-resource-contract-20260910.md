# R65 Yinluo Resource Contract

Status: candidate runtime integration under offline validation; not acceptance.
Parent: `rebuild-stability-20260907.md`, R65 and R66.

Production, user settings, services and skill remain unchanged. CommandAttempt
stays shadow-only. This work does not implement the unapproved R07 shared
transport architecture.

## Scope

The resource boundary is cultivation, sha and unrefined soul stock, including
their consumers outside `yinluo.py`. A conversion-only patch cannot close R65.
Slot lifecycle and send ownership must agree with resource reservations; an
HTTP read or local timeout is not an action receipt.

| Operation | Resource evidence | Required treatment |
| --- | --- | --- |
| Conversion success | Explicit cultivation cost and sha income, possibly bonus sha | One fact per component and original command; no text-only or last-ID dedupe |
| Conversion backlash | Explicit cultivation loss; no successful conversion income | Do not charge the cost and backlash description twice |
| Soothe | Explicit cultivation cost, slot count | Missing cost is unknown, not an assumed 50 |
| Daily sacrifice | Primary and optional bonus sha income | Same-day policy is distinct from financial event identity |
| Demon summon start | Existing parser recognizes a message saying cultivation was consumed | A requested amount is not a debit; an explicit consumed amount is |
| Demon summon backlash | Recorded real reply says cultivation dropped by 1362 | This is additional to any proved start cost, not a replacement for it |
| Demon summon / blood forest success | Named unrefined soul income | Do not infer a quantity from an unrecognized sentence or confuse lineage growth with stock |
| Refine send | Slot and resources expected to be used | Reserve only; do not overwrite the balance or soul inventory |
| Refine success | Named soul moved into a slot; sha cost is absent in the recorded success format | Soul debit is explicit; an estimate of 400/1000 is not a confirmed sha debit |
| Refine failure / timeout | Explicit rejection versus outcome not known | Release only a proved rejected reservation; timeout does not restore pre-send snapshots |
| Wanxin banner / strip | Explicit sha cost when present | Debit the provider identified by the original command, not the beneficiary or configured current helper |
| Wanxin strip failure | A recorded fixture includes provider cultivation loss as well as sha cost | Account both resources, with strict actor identity and reply ownership |
| Banner panel | Absolute sha and soul observations | Native source order must reconcile earlier/later deltas; absence of a parsed field is not zero |
| MiniApp panel | An owned response but no established server resource revision | Remains observation-only for financial authority until the server contract is proved |

The recorded strip-success fixture omits sha cost. The existing fallback of
120 is a planning estimate, not proof that this particular result charged it.
Likewise, blood-forest cost semantics need evidence rather than an invented
zero or mandatory sha debt. The fact parser records an explicit cost when
present, but the existing wording alone proves neither a cost nor free use.
Known protocol costs may bound reservations, but they cannot masquerade as
observed debits. For a strictly owned conversion result, a reported actual
cost that differs from the requested amount remains the financial authority;
the difference is diagnostic, not permission to replace the charge.

## Fact Contract

1. Require an official bot, an admitted game chat, native Telegram message or
   edit time, and an unedited original command. The command sender must resolve
   to one managed identity. A display-name match or family label alone is not
   proof for second-person replies.
2. Keep identity/account, chat, original command and result revision separate.
   Two chats with the same message ID are independent. Rebinding, replacement
   or conflicting ownership cannot transfer an in-flight result to a new owner.
3. Split phases into semantic components. A summon start cost and subsequent
   backlash are different debits of one operation. A later final edit that no
   longer repeats the start cost does not by itself revoke that earlier fact.
4. Distinguish explicit effects from components still requiring evidence.
   Missing, malformed or contradictory amounts are unknown, never zero. A
   delayed start receipt may supply a missing component without becoming a
   second operation. Duplicate messages and edits of the same component must
   revise one fact instead of adding another charge.
5. A resource snapshot overlapping an execution interval cannot prove whether
   it includes that debit. Do not assign an API request-start clock to a
   server balance. A sufficiently later native baseline may reconcile unknown
   prior consumption, but it does not retroactively prove an action's outcome.

## Reservation And Persistence Contract

- Keep reservations outside observed balances. Available resources are verified
  balance minus live reservations, not a mutated balance restored on timeout.
- The shared cultivation consumer view must include Yinluo reservations so a
  queued conversion and duel cannot both spend the same cultivation.
- Each reservation belongs to an identity object/account and one concrete
  command. Check it at preparation, after awaits and at actual dispatch.
- Definite non-send or an owned rejection may release it. Unknown send,
  cancellation after dispatch and silence require retained ownership and
  bounded reconciliation, not automatic spending retries.
- On confirmation, reconcile the resource facts and reservation together before
  yielding. No old full-state snapshot is copied back over unrelated income,
  consumption, slot changes or UI settings.
- Project cultivation and sha/soul facts into one normal persistence transaction.
  Corruption, unknown legacy provenance, account migration and capacity overflow
  must be visible holds; never reinterpret them as empty valid history.
- Do not evict uncovered facts or reservations in a way that permits replayed
  charges or fresh sends. Covered financial history and unresolved command
  ownership have different retention requirements.

## Implementation Sequence

- [x] Strict financial reply facts and native ownership admission, covering the
  complete operation table above. No new live writer at this stage.
- [x] One projected sha/soul book, shared cultivation facts and reservations;
  include corrected/replayed/withdrawn components and late absolute snapshots.
- [x] Switch the scoped Yinluo and Wanxin financial writers together, remove optimistic
  debit/full-snapshot rollback, and preserve manual actions and disabled-module
  result reconciliation through the real native/replay paths.
- [ ] Resource admission through manual and scheduled sends, early replies,
  queued sends, unknown outcomes and account/config changes.
- [ ] Temporary-SQLite rollback/reload, migration/retention evidence, broad
  regression and final review. Production validation remains separately gated.

## Required Replays

- Conversion A, B, repeated A; same message IDs in two chats; a corrected amount.
- Delayed baseline before/after a debit, same-second ambiguity and missing native
  time. A later result must not erase a separately proved start charge.
- Duplicate soothe; summon start plus backlash; explicit zero versus missing cost.
- Refine reservation, unrelated sacrifice income and external Wanxin consumption,
  followed by denial, timeout or success. None may restore the old whole balance.
- Wrong slot/target, multiple participants and username changes. Provider-only
  costs must not be charged to a beneficiary.
- UI reads, external profile writes, disabling a module, rebind/replacement,
  deletion, cancellation and persistence failure at each relevant boundary.
- Empty/partial MiniApp panels and old/corrupt state cannot authorize spending.

R65 remains open until these contracts are implemented on the real call paths,
not merely represented in a parser or demonstrated with isolated numbers.

## Foundation Checkpoint

The first checkpoint had three independent, non-runtime components. The
runtime integration below supersedes that checkpoint's integration status:

- `model/yinluo_resource_facts.py`: strict command and resource facts, separate
  charge/outcome revision scopes, named-provider/beneficiary qualification,
  native original-command admission and partial absolute-panel parsing.
- `model/yinluo_resource_replay.py`: bounded decoding of existing log batches,
  including empty edits and official intermediate reply nodes. It neither
  reads files nor requests Telegram history, and cannot use local log clocks
  or a `sent` bookkeeping row as original-command evidence.
- `model/yinluo_resource_book.py`: staged sha/soul projections plus the existing
  caller-supplied cultivation ledger. No duplicate cultivation store, state
  mutation, save, send, retry, switch change or scheduler invocation exists.

The native resolver contract is exercised through `_resolve_event_reply` and
`from_telegram_event`, not only hand-built dictionaries. `send_as_id=0/None`
may be an unresolved routing hint; a uniquely owned native sender still wins.
Bool/float/string hints, contradictory sources and an edited current command
cannot supply financial authority. Named assistance costs are always checked
against the original provider, never assigned to the beneficiary or whichever
helper is configured now. Unique aliases may confirm a rename.

The book retains original command/result scope and rebuilds projections from
facts. Out-of-order charge/progress/outcome messages, conversion corrections,
removed bonuses, changed/unknown soul types and blank edits are covered.
An unknown edit of a retained official message may invalidate the old facts
even when the original command cannot be fetched, but that fallback cannot
credit the new text or change the recorded owner. Conflicting same-clock edits
remain unknown. Derived cache values that disagree with the journal are not
spendable. A duplicate known receipt can rebuild a damaged projection without
inventing a new financial event.

At the foundation checkpoint there were no send reservations, caller
integration, new SQLite save path or timeout/lifecycle replacement. A native
panel may cover a prior unknown financial effect without proving whether the
gameplay operation succeeded. Missing panel fields and omitted soul types
remain absent; the parser does not infer a complete inventory or any zero.

Capacity is bounded, not solved operationally: 256 receipts and 64 concrete
resource types per book; 8192 records and 8 reply links per input log batch;
the existing scalar ledger retains its own 256-entry/16-source limits. A
book-level overflow or conflicting command owner retains existing evidence
and marks a visible hold. A fresh panel does not silently clear those holds
or retire unresolved ownership. Source-only scalar overflow may be covered by
a sufficiently later native balance as already specified by that ledger.
Operational retirement, latency at capacity and cross-batch ownership must be
reviewed before runtime adoption. These holds are not acceptance of automation.

Foundation tests are `test_yinluo_resource_facts.py`,
`test_yinluo_resource_replay.py` and `test_yinluo_resource_book.py`. Final
verification counts and artifact paths are recorded in the parent audit.

## Candidate Runtime Checkpoint

`yinluo_accounting` now owns the candidate's resource book, reservations and
business source points. It shares the existing cultivation ledger and commits
both resource projections and provider/beneficiary observations through the
normal SQLite transaction. Failed writes restore only those staged fields.
The same schema validation runs on read and before write, including operation
phase/receipt consistency and the 120-point business bound.

All scoped Yinluo sends and the two resource-consuming Wanxin assistance
commands reserve before sending. Definite non-send releases the reservation;
unknown send or cancellation does not. Actual runtime finalize/detached-RPC
tests cover receipt adoption after SQLite reload. An exact operation UUID,
module, command and chat link the receipt to its account-bound book. Runtime
pending rows do not currently contain account_id; optional contradictory
account metadata is rejected rather than requiring an absent field.

Incoming native events and bounded local log replay feed the same projection.
The file reader uses at most two daily log tails, 1 MiB per file, 8192 decoded
records and a 24-hour window. It does not request Telegram history. Original
sender and server time must exist; a bookkeeping `sent` row cannot provide
them. Unrelated edits do not rebuild every identity's book.

Completion is separate from whether the event changed state. A duplicate
committed final reply remains handled; an unknown edit of the final result
reopens only its original operation. A later edit of a summon start charge
does not reopen its separate final outcome. Manual results can close their
exact pending/guard without a script operation record. Native resource
reconciliation also owns financial pending timeouts before generic retry or
silence cleanup; no result means a retained operation and bounded recheck.
Only read-only banner queries may expire without an outcome.

R114 adds scoped atomic read expiry and exact shared pending cleanup, including
already-expired records after reload. The known send receipt owns the deadline;
an expired unbound query can adopt its original late receipt. Active callers
and exact registered RPC handles prevent premature expiry/replanning, while
failed cleanup cannot discard the expired operation. Consuming reservations
remain untouched. See `R114-yinluo-read-lifecycle-20260914.md`; associated/full
offline regression passes with 955/12269 cases respectively. Legacy migration,
terminal cleanup crash boundaries and production acceptance remain open.

R115 follows through on completed-pending cleanup: native completion and exact
pending deletion share the resource transaction, including staged retirement.
Old completed rows reconcile from validated hot/cold evidence without old logs;
enabled scheduling does so before due-time checks. Guard cleanup checks exact
account/chat/message/command/send-time ownership and preserves real remote
cooldowns through the existing guard helper. See
`R115-yinluo-completion-cleanup-20260914.md` for scope and verification evidence.
The final associated suite passes 1102 cases; the network-isolated full suite
passes 12324 cases and 1275 subtests. This does not close unproved legacy/no-ID
intents, read calibration under retained holds, complete forced-stop/storage
recovery, capacity or production acceptance.

R116 separates read lifecycle from financial authority under retained holds.
An exact stored native banner reply can close its read operation/pending/guard
without clearing a resource hold, gap or consuming reservation. Scheduler-local
recovery and expiry precede hold/due-time returns; sticky book gaps no longer
trigger ineffective automatic queries. Manual dispatch performs local completed
guard cleanup before shared admission. See
`R116-yinluo-held-read-lifecycle-20260914.md`. The 78 new cases and associated
1627-case/311-subtest suite pass; full isolated regression passes 12402 cases
and 1275 subtests. This does not
auto-resolve legacy holds, discard history at hard capacity, or close the
remaining migration, complete forced-stop/storage and production gates.

R117 preserves legacy observation pending evidence through normalization,
configuration, unavailable-module cleanup, MiniApp status admission and the
observation codec's SQLite reload. Its additive `legacy_pending_invalid`
boolean prevents corrupt roots from becoming trusted empty state. Existing
books respect newly encountered untracked legacy pending, and a new panel
does not replace an unowned held business summary. See
`R117-yinluo-legacy-evidence-20260914.md` for compatibility/rollback constraints.
The 253-case focused suite, 1880-case associated suite and static checks pass;
full network-isolated regression passes 12524 cases and 1275 subtests. The
checkpoint adds 122 cases. This is evidence preservation, not automatic legacy
outcome migration or permission to clear a hold.

R118 adds a scoped migration for old Yinluo action tags backed by the exact
shared pending row and a retained native terminal receipt. Every old slot must
have unique command/time/target ownership; unowned summaries, malformed keys or
aliases, unrelated unresolved work and stronger holds remain blocked. Facts,
old pending removal and hold transition share the resource transaction, with
type-sensitive staged-state validation and local hot/cold proof revalidation.
No old optimistic balance is restored and no consuming retry is introduced.
The 128-case migration suite and 2058-case/372-subtest associated suite pass;
the full network-isolated suite passes 12652 cases and 1275 subtests. See
`R118-yinluo-legacy-migration-20260914.md`. General no-ID reconciliation,
forced-stop/storage, operational capacity and production acceptance remain open.

R119 follows through on normal operational capacity: the existing automatic
plan can use a reserved native banner query when sha/named-soul coverage would
allow terminal retirement. Missing cultivation, unknown outcomes, holds and
full query slots do not cause repeated ineffective requests. Exact expired
query evidence moves to a strictly scoped v2 cold archive without changing its
baselines, conflicts or business points; expiry and pending cleanup are atomic.
See `R119-yinluo-capacity-lifecycle-20260914.md` for evidence and compatibility.
The final focused suite passes 82 cases, the associated suite passes 2003 cases
and 323 subtests, and full isolated regression passes 12734 cases and 1275 subtests.
This does not release consuming unknown work, repair missing facts or certify
the 120-point business bound, cold disk capacity or production behavior.

R120 adds native-backed business-point retention for distinct assistance
beneficiaries. Only exact originally bound, completed assistance clocks may
move to the cold provider/account/key index. A persisted count/digest manifest,
paired hot/cold moves and native-proof revalidation under the SQLite writer
lock preserve old boundaries through late replies and storage races. The
78-case focused suite passes, including 122 distinct beneficiaries at the
unchanged default 120-point hot bound. See
`R120-yinluo-business-retention-20260914.md` and the parent audit for associated
and full regression. The latest isolated full suite passes 12812 cases and
1275 subtests after the staged-slot guard. This additive table/JSON field
requires joint hot/cold snapshot and rollback review; it does not clear ambiguous old holds, reclaim
unproved manual clocks or authorize deployment.

Wanxin assistance records retain the beneficiary identity/account, commission
ID, publication and acceptance times. The provider still pays a confirmed
cost if that beneficiary changes, but the old result cannot clear the new
commission. Completed assistance bindings are retained across subsequent
sends; deleting them would reopen a cross-account replay path. Owner names
are explicit, not taken from the listener's current identity context. Both
cooldown and commission-invalid replies use the native contract even without
a family hint; old financial business branches have been removed.

Offline coverage includes 558 focused cases and 13 subtests at this checkpoint.
The parent audit records full-suite and final static-check artifacts. This is
candidate code only: no service, production database/configuration, skill or
game request was touched, and no deployment or push was performed.

## Remaining Acceptance Gates

- R118 resolves only exact tagged legacy sends with retained native terminal
  proof. Other legacy pending work still holds until its original ownership
  and outcome can be reconciled. A fresh balance cannot close an unknown action. Missing
  native clocks, truncated log tails and missing old commands cannot be
  replaced by local receipt clocks or invented zero costs.
- Covered, terminal receipts/operations now have a candidate retirement path;
  see `R65-yinluo-retention-contract-20260911.md`. Original assistance bindings,
  including the absence of a manual binding, survive retirement, restoration
  and repeated edits after reload. The hot bounds are still 256 receipts and
  64 operations, with one operation/reply slot reserved for a native read.
  R119 covers useful native calibration at ordinary rollover and exact expired
  query archival. R120 covers the 120-point business bound for native-backed
  completed assistance and distinct-beneficiary churn. Unproved manual points,
  legacy capacity gaps and already-full uncovered books without a read slot
  still require a separate capacity/migration policy.
- Cold evidence has no automatic expiry and grows on disk. Indexed lookup,
  per-command payload bounds, transaction rollback and small-capacity cycling
  are tested, but disk monitoring/export, complete storage failure handling
  and full-capacity replay/manifest-scan latency remain deployment gates. No balance can
  retire an unknown business outcome or authorize mutation replay.
- The complete queue/forced-stop lifecycle, pre-existing pending migration,
  missing/changed account handling, and capacity/replay latency still require
  review. R07's shared no-ID transport architecture remains unapproved.
  R113 now verifies pre-dispatch cancellation versus unknown outcomes and
  completed/archived transport-return finalization, including immutable native
  message bindings, SQLite release rollback and owner invalidation. It adds no
  retry controller and does not release a killed process's unproved intent;
  see `R113-yinluo-send-finalization-20260914.md`.
- R73 now covers native external-commission log decoding and the two external
  recovery consumers. Nonfinancial Wanxin direct replies, owner/accept/identify
  sends, early receipts, timeout cleanup and scalar anchors remain in the
  broader module/R11 review. Rejecting stale or incomplete log evidence must
  not be treated as permission to cancel or republish; the scheduler's fallback
  still needs current-commission ownership and durable outcome reconciliation.

Do not deploy or close R65 on the strength of a green offline suite while
these gates remain open. R66's MiniApp banner remains observation-only for
financial authority; inventory API reads remain explicit UI operations.
