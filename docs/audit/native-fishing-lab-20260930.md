# Native Dwelling Fishing Lab (2026-09-30)

## Status

Worktree: `/root/xiuxian-native-fishing-20260930`.
Branch: `lab/native-fishing-20260930`, base `8a04ebaa`.

The branch now contains a complete single-round worker, a version-2 native
journal, checked SQLite persistence, atomic result accounting and an explicit
public-entry canary action. **It has not been deployed or enabled for scheduled
production fishing.** Production stays at `8a04ebaa`.

The new record is `fishing_native_operation`, not the legacy `fishing_operation`.
Normal public-entry calls retain their existing path until acceptance; only the
explicit `fishing_native_canary` action or an unresolved native record enters the
new runner. Original UI pond/bait settings and the fishing toggle remain in use.
No live cast or purchase has been sent.

User sequencing constraint: finish and stabilize this rollout first, then
clean debt. Do not combine unrelated cleanup with the deployment. The canary
code being testable is not evidence of live catch acceptance.

## Sources

- wxjerry `origin/main` remains `aa9dba29` on the latest SSH check.
- Absorbed protocol/physics from `3c77db65`; voyage eligibility from `3c220736`.
  Did not import upstream global state, mutation replay, pauses or test deletion.
- Current official controller:
  `/staticweb/assets/dwelling-fishing-controller.js?v=fishing-v14-world-moon-cue`.
  SHA-256: `07d36c165f9d62bda1c7f38a72cada16f63f4fca9fb02ecc345e67304dc95237`.
- Unlike upstream's saved holding state, current frontend recovery releases the
  line at the next 20 ms tick. The Lab preserves old events and applies this
  release before resuming control. A completed checkpoint adds no future input.
- Proof events represent physics inputs. The UI's cosmetic button release after
  final simulation is not appended as an ambiguous last-tick physics input.
  Server acceptance still requires a controlled real-cast test.

## Implemented and Tested

- `fishing_dwelling_protocol.py`: strict context, quota, bait, rod, owned session,
  settlement and checkpoint parsing. No truthy success flags, inferred fish,
  filtered corrupt events or fabricated quota consumption.
- 20 ms V1/V2 physics, steady/leap/surge behavior, bounded checkpoint events and
  restored state. Independent JavaScript formula replay includes numeric, ASCII
  and non-BMP seeds, three fish powers and resumed checkpoints.
- Server time uses monotonic elapsed time plus half RTT, capped at 250 ms.
  Expired bite selects `state`, never another hook/cast. Timed proof iteration
  waits actual duration, checks ownership after waits and rejects stalled clocks.
- `fishing_dwelling_journal.py`: version-2, bounded single-round record with exact
  identity/account/player/cast/session ownership. Cast, hook, checkpoint and
  fight intents are saved before dispatch. Recovery increments the revision;
  stale queries, backwards phases and changed challenges are rejected.
- Unknown mutations only query their original session. An unchanged phase does
  not permit another hook/fight. A lost checkpoint ack requires the matching
  server checkpoint before further control inputs. Explicit zero-attempt
  preparation/budget/cancellation results may compensate their own unsent
  intent; this cannot erase a cast recovered from a previous process.
- `fishing_dwelling_store.py`: main-loop compare-and-save bridge with an
  independent 128 KiB JSON column. Tests use real temporary SQLite, reload each
  pending phase, and check DB intent before allowing worker dispatch. Damaged
  JSON remains a hold; a replaced/rebound owner cannot write. Both legacy
  pending-operation and pending-projection records block native admission.
- Fish gains and the accounted marker share one persistence transaction.
  Quantities do not inflate rod count: two fish from one cast remain one rod.
  Scoped settlement context supplies daily quota; stale-day quota cannot reset
  current-day counters. Changed fact/inventory bases block duplicate projection,
  while disabled UI settings retain gains without rescheduling. Save failure
  rolls back inventory and keeps the receipt.
- `fishing_dwelling_miniapp.py`: one cast only, shared HTTP budget/global limiter,
  no automatic mutation retries. Request timing excludes local queue/limiter
  waits from RTT. Cancelled callers drain the in-flight worker and retain its
  confirmed receipt; cancellation cannot release locks before HTTP returns.
- `fishing_dwelling_runtime.py`: explicit public-entry canary, verified directory
  and selected model, original pond/bait plan and both existing exclusion locks.
  No fallback to the legacy worker after native admission or an unknown result.
- A restarted/unresolved cast exposes an exact `state` query, not another cast.
  An unscoped `context`, foreign session, stale revision or negative/missing
  response cannot clear the record. Confirmed settlement is retained and cannot
  regress or change rewards; the journal itself does not project inventory.
- Missing rod, missing bait, quota exhaustion, voyage and other-mode conflict
  remain distinct identity-local reasons, not a global circuit breaker.

## Real Read-Only Evidence

`/root/xiuxian-native-fishing-contract-20260930.json`, 18:24 CST:

- Identity `3765328695`, account `301299112`, player `-1003765328695`.
- Owner start, selected-player start, details and fishing context all HTTP 200.
- Entry is `fishing/integrated`; rod present, quota 0/5 used, 5 remaining,
  no active session, no conflict.
- Five bait types, all count 0. The new parser accepts the contract and produces
  `fishing_bait_missing`; this is not an entry outage or missing-rod error.
- Production fishing for this identity was already off. No toggle, bait purchase
  or gameplay mutation was performed. Context quota before/after stayed 0/5.
- Probe now saves allowlisted bait metadata, preserving missing fields instead
  of turning malformed/missing inventory into an empty successful snapshot.

Additional enabled-identity preflight, all four reads HTTP 200 each:

- `/root/xiuxian-native-fishing-canary-context-7538826434-20260930.json`:
  identity/account/player `7538826434`, rod present, five bait types all zero,
  no conflict, no active session, quota 0/5 used.
- `/root/xiuxian-native-fishing-canary-wa-context-20260930.json`:
  WA `8659059191`, rod present, bait zero, `fishing_companion_sailing`.
- `/root/xiuxian-native-fishing-canary-baji-context-20260930.json`:
  Baji `301299112`, rod present, bait zero, `fishing_companion_sailing`.
- These are eligibility failures, not MiniApp outages. Do not force a hook,
  alter voyage automation or buy an arbitrary bait to produce a green report.

## Required Before Rollout Completion

1. Validate the implemented `buy-bait`/`chum` receipts against one controlled
   live supply response. Offline implementation and cost evidence are complete;
   purchase acceptance is not implied by successful context reads.
2. Deploy only the validated canary surface, preserve a backup and rollback
   boundary. A rollback after a live mutation must retain the native ledger;
   older production code does not understand its unresolved records.
3. Controlled one-rod acceptance on an already-enabled identity with rod, bait
   and companion available, followed by real recovery/settlement verification.
   Confirm server checkpoint/proof acceptance, not just offline success.
4. Verify voyage-return scheduling and original daily follow-up behavior before
   scheduled rollout. WA/Baji must not skip or interrupt voyages to force a test.
5. Only then enable integrated-directory routing by default and observe natural
   execution. Retain legacy unresolved recovery; delete old paths in a separate
   cleanup after stability, as requested.

Deferred monitoring debt: the 18:57:59 journal line about an unowned external
Xuangu question timing out caused a generic observer warning. It was not our
identity's failed attempt. No shared observer patch was applied in this batch.

## Supply Batch and Canary Boundary

- Single-cast persistence batch committed/pushed as `1c9dfd07` on the Lab branch.
- `fishing_dwelling_supply.py` validates actual shop prices, materials, counts,
  eligibility, active chum and daily limits. It reserves the total planned bait
  purchases plus chum cost before the first purchase. It does not reuse legacy
  price constants or purchase rods or arbitrary materials.
- A separate bounded `fishing_native_supply` record saves the exact operation ID,
  owner, placement, before/after evidence and inventory basis. Unknown replies
  remain pending without purchase replay, cast, legacy fallback or ledger erasure.
- Direct scoped purchase confirmation requires the exact affected resource
  changes; chum also requires the daily counter and active effect. Inventory
  projection and its accounted marker share one checked save. Restart, owner
  replacement, save failure and in-flight cancellation are covered with SQLite.
- Each invocation performs at most one supply action or one cast. Manual canary
  invocations retain the original next-fishing timestamp. A supplied result is
  not a completed rod. An old accounted rod cannot mark a later failed request
  as successful. Ordinary scheduled routing remains unchanged before acceptance.
- Read-only shop evidence:
  `/root/xiuxian-native-fishing-shop-7538826434-20260930.json` (all four HTTP 200).
  Actual configured plan: spirit-rice bait, auto-buy 20, rice chum. The first
  purchases would be 20 spirit-rice bait (700 stones) and 20 plain bait (240
  stones), then rice chum (2 plain bait plus 30 stones). These are observed
  prices, not hard-coded future prices. Each action re-reads the live context.
- Existing enabled identities are due around 2026-10-01 00:00. A manual canary
  must not pull those timers forward, enable other identities or interrupt WA's
  01:29 wild-training preparation. Supply/cast locks remain shared with production.
- Rollback: before any gameplay mutation, return to `8a04ebaa` if necessary.
  After a mutation, retain both native records and their recovery-aware code;
  never restore a stale DB backup or an old sender over unresolved operations.
  Disable further canary invocation and investigate the retained receipt first.

## Verification

- Initial batch: 117 native tests; 901 fishing tests plus 17 subtests.
- This batch: 194 native tests after unsent-intent compensation.
- Expanded fishing/persistence/state/cave/UI run: 2144 passed, 264 subtests,
  including the final two compensation cases (33.42 seconds).
- Supply batch plus final timer boundary: expanded suite 2200 passed,
  264 subtests (39.13 seconds). Live shop fixture validates without mutations.
- JavaScript syntax, Python compileall and diff whitespace checks passed.
- Isolated protocol/journal imports load no `model.runtime`, `model.state`, `model.config`,
  `requests` or `telethon`.
- Production observer remained ok through 19:59; PID `3597994`, NRestarts 0.
- Before canary deployment, 20:55 observer and watchdog were both ok; the main
  checkout remained clean at `8a04ebaa`, with no pending tasks.
- 8-hour defensive preflight: pending queue empty. WA wild training due
  2026-10-01 01:29 CST, not yet in its Tianxing preparation window. Listener
  sidecar inactive remains a known watch item; main listener is active.

World Boss stays off; both incense-to-shenshi settings stay off. Channel-send
freeze, manual-only inventory policy and CommandAttempt shadow control remain
unchanged. Do not treat this Lab commit as a production migration approval.
