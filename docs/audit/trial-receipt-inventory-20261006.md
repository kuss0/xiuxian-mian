# Trial Receipt Inventory

## Scope

`tools/trial_receipt_inventory.py` is a read-only prerequisite to the durable
trial reporting work. It is not a reward ledger, scheduler, recovery mechanism
or notification sender. It reuses the current operation/archive validators
rather than maintaining a weaker second copy of their schema.

The tool reads ownership and retained slots in one WAL-aware SQLite read
transaction (`mode=ro`, `query_only`, explicit `BEGIN`). It neither creates nor
migrates a database. Missing runtime rows and orphan runtime rows are visible;
missing account mappings are not filled from a default account or username.
It needs the repository's usual import configuration, or the isolated test
environment, but does not log in to Telegram or call MiniApp endpoints.

## Report Meaning

- `status=ok` means no structural/binding/collision warning in the retained
  inventory. It does not mean that a game action, daily batch or delivery has
  completed. `current_complete_phase` is only the stored phase label; pending
  requests and unacknowledged saves are counted separately.
- Account binding compares the stored identity/account with the current
  explicit mapping. A stored player ID is not independently proven game-role
  identity. Player binding and parent-batch ownership remain unverified.
- Current receipt counts and historical unknown-archive receipt counts are
  separate. No daily reward total is produced. Missing overwritten operations
  cannot be recovered from this snapshot.
- The same operation ID under multiple owners or incompatible immutable
  receipt prefixes is a conflict. The same scoped round key in different
  operations is ambiguous, not deduplicated automatically. Identical reward
  contents in different rounds/identities are not collapsed.
- Capacity measures serialized slot bytes, including measurable malformed
  values; unmeasurable values are counted explicitly. It is not SQLite page
  usage or a forecast of a future completed-operation archive.
- Output contains numeric IDs, fixed classifications and counts, not raw
  receipts, tokens, profile labels, exceptions, credentials or reward contents.

## Read-Only Production Evidence

At approximately 15:35 CST on October 6, the Lab CLI read the production DB:

- 24 current records validate and match their explicit identity/account map.
- 72 current receipts, 24 complete-phase markers, no pending request/save.
- One valid historical unknown archive, containing zero settled receipts.
- No operation-owner, immutable-prefix or cross-operation round-key conflict.
- Current serialized slots total 28,504 bytes, maximum 1,189 bytes per identity.
  Archives total 1,002 bytes, maximum 956 bytes. Both configured per-identity
  limits remain 262,144 bytes. No limit or retention setting was changed.

Report: `/tmp/xiuxian-trial-receipt-inventory-20261006.json`.
These are retained-slot observations, not proof of all-day reward completeness.
No operation was replayed, archived, deleted, adopted into a parent or notified.

## Validation

Initial tests: 31 passed; expanded cross-module pass: 385 passed. Second review
found that an empty orphan runtime row could otherwise look benign; it now
reports an explicit warning. Added WAL visibility, concurrent snapshot ownership,
invalid-capacity and malformed-row tests. Final focused pass: 188 passed.

Final whole-suite acceptance: 16,331 passed / 1,461 subtests in 465.88 seconds.
JUnit: `/tmp/xiuxian-trial-receipt-inventory-20261006.xml`. Final second-pass
cross-module regression: 214 passed. Ruff, compilation and whitespace checks
passed. All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. This is a maintainer
review, not an independent external audit.

## Still Open

Follow `trial-report-handoff-plan-20261006.md` for strict parent ownership,
durable per-round handoff, idempotent projection and bounded retention. This
inventory does not implement those requirements and does not justify removing
the remaining child reward notifications. Existing held notification batches
and all runtime account switches remain untouched. No main-service restart
is needed to use the CLI; prior staged runtime fixes remain unloaded.

At 15:43 the main supervisor remained PID 2207210, NRestarts=0; observer only
reported the two existing held notification batches. The current worker is
still 2207227 / `f7958deb`. The old duel path again deferred Lpprceqei's missing
baseline to 16:11:00; its staged calibration repair is not live acceptance.
Both incense-refining switches remain off, with harvest due at 16:21/16:35.
