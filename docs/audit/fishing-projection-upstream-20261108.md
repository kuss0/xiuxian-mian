# Native fishing projection and wxjerry comparison

Base production commit: `2f1eeca1`. Candidate: `fix/fishing-projection-20261108`.
Production evidence: `/root/xiuxian-fishing-audit-20261107/REPORT.md`.

WA2000's November 6 second rod (session 60644) has a complete settled receipt
but the local inventory digest changed before accounting. Its fishing fact
digest remains unchanged. The normal scheduler repeatedly loads the public
entry and details, then retries the same impossible local projection. By
November 7 22:59 this had generated 851 failures and 1702 entry requests that
day. The exact inventory writer cannot be reconstructed from a digest alone.

## Changes

- New v4 receipts retain item before counts. Confirmed gains can be projected
  despite unrelated inventory changes only when fishing facts and every
  affected fish, reward and bait count still match. Related changes remain
  held. v2/v3 receipts retain their conservative whole-inventory comparison.
- Empty rods still need the existing fresh, scoped state read before rebasing
  bait balances. Missing/wrong-session readbacks cannot close that receipt.
- A held complete receipt is excluded from automatic background admission and
  rejected before loading the public entry. Explicit original-rod recovery
  remains available. No timer extension or global/module switch is introduced.
- Read-only health includes both native receipts and the explicit projection
  error, including unresolved receipt counts.
- An explicit maintenance tool can reconcile a prior-day confirmed receipt
  against a fresh complete server inventory with exact owner/cast checks. It
  requires a stopped worker and a verified SQLite backup. The original receipt
  archive, absolute inventory replacement and accounted marker share one
  transaction. It adds no estimated fish/rewards and no rods to today's count.
- The one-shot read-only probe now exports an allowlisted complete inventory
  for that maintenance tool, without exporting credentials.

## wxjerry upstream

SSH fetch on November 8 advanced the reference `origin/main` from `d466ae4`
to `286e7ab` (latest upstream commit dated October 17). The reference working
tree was not reset or merged into production.

| Upstream source | Local disposition |
| --- | --- |
| `fishing_dwelling.py`: original cast lookup and refreshed server context | Adopt the recovery principle with a separate bounded maintenance read; retain local no-blind-replay and atomic accounting guards |
| `cd01b78`: core/overview/deferred inventory placeholders are incomplete | Port rejection to the complete inventory parser and maintenance probe; test that placeholder empty lists cannot erase a bag |
| `cd01b78`: player-bound inventory and serialized identity selection | Local complete snapshot parser already checks ownership; retain its existing entry locks and do not add polling retries |
| `2077dc5`: early-summary cleanup and retry timing | Existing local lifecycle differs and already has scoped receipt-time/reply recovery; no shared dispatch/listener rewrite included |
| Longwind trial / Xuangu dungeon additions | Listed as separate feature work; not enabled or copied into the current fishing repair |
| Avatar/rebirth route changes | Different identity/runtime model; not transplanted into production guards |

There are no newer upstream fishing code changes in this incremental range.
This delivery does not claim that all wxjerry functionality has been absorbed.

## Validation and release

Grouped regression passed: 194 legacy caller/routing tests; 1167 fishing,
native receipt/report and probe tests (21 subtests); 407 health, inventory and
maintenance tests (21 subtests). Ruff and `git diff --check` passed.
The first combined collection stalled in a legacy cancellation test; that
test and its 194-test group passed when rerun independently. The regression
groups use fresh processes rather than treating that stalled run as a pass.
All tests use the repository's temporary DB harness; live-test DB access is
not enabled. Preserve the live quiz-bank learning change.

At 00:48:52 a bounded four-request read confirmed a complete matching WA
server inventory: 青鳞小鲫 160 and 灵米饵 8, versus saved local 159 and 9.
Proof: `/root/xiuxian-fishing-audit-20261107/wa_inventory_probe_20261108.json`.
The maintenance preview must still pass against the stopped worker's final
state before apply; later release acceptance is recorded separately.

## Production acceptance

`a066b441` deployed and pushed to `xiuxian-mian/main`. The worker was stopped
for reconciliation and the fresh proof passed against its final persisted
state. The backup is mode 0600:
`/root/xiuxian-fishing-audit-20261107/chaogu_before_fishing_reconcile_20261108.db`.
Archive: `fishing_reconciled_inventory:8659059191:d57a7a5add704acb9e43475893174516`.
The old receipt remains available in that archive; the active marker is
accounted. Local 青鳞小鲫=160 and 灵米饵=8 match the server read, with no
estimated gains and no today rods added.

After restart at 00:50:12, the normal public scheduler read a new fishing
context at 00:51:38 and entered `fishing_companion_sailing` wait. Its next
fishing time is 01:42:04, after the saved voyage return at 01:41:04. No further
projection error or repeated entry read appeared during the observed window.
HTTP UI returned 200. The read-only observer reports no abnormal module or
native receipt pending; unrelated historical/other-module warnings remain.

Final compatibility review added the same placeholder rejection to
`has_complete_cave_inventory_snapshot`, so overview rendering ignores a
deferred bag instead of invoking the strict inventory parser. Its three
core/overview/deferred fixtures first failed, then passed with the guard.
The updated guard, overview, native entry and maintenance regression passed:
245 tests / 5 subtests. This follow-up is deployed with an explicit restart.

Natural new-rod execution after the companion returns has not yet occurred
in this acceptance window. Do not count the repaired old receipt as a rod
caught today.

Do not roll back to a v2/v3-only reader after new v4 rods have started. During
an early rollback, stop the worker and inspect actual generated receipt
versions before selecting code/state recovery.
