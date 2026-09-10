# R65 Yinluo Resource Contract

Status: candidate design and offline construction; not runtime acceptance.
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
- [ ] One projected sha/soul book, shared cultivation facts and reservations;
  include corrected/replayed/withdrawn components and late absolute snapshots.
- [ ] Switch all Yinluo and Wanxin financial writers together, remove optimistic
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

## Offline Construction Checkpoint

The candidate now has three independent, non-runtime components:

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

This only partially implements the second checklist item. There are still no
send reservations, no real caller integration, no new SQLite schema/save path,
no timeout/lifecycle replacement and no migration/archival policy. A native
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

Current tests are `test_yinluo_resource_facts.py`,
`test_yinluo_resource_replay.py` and `test_yinluo_resource_book.py`. Final
verification counts and artifact paths are recorded in the parent audit.
