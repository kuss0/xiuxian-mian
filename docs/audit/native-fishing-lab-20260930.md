# Native Dwelling Fishing Lab (2026-09-30)

## Status

Worktree: `/root/xiuxian-native-fishing-20260930`.
Branch: `lab/native-fishing-20260930`, base `8a04ebaa`.

The branch contains a single-round worker, checked SQLite persistence,
atomic result accounting and an explicit
public-entry canary action. **The canary surface was deployed as `4fbefa7f`
at 21:02 CST; ordinary scheduled production fishing has not been migrated.**

The new record is `fishing_native_operation`, not the legacy `fishing_operation`.
Normal public-entry calls retain their existing path until acceptance; only the
explicit `fishing_native_canary` action or an unresolved native record enters the
new runner. Original UI pond/bait settings and the fishing toggle remain in use.
Three controlled supply actions and one cast were sent on `7538826434`.
The cast exposed a numeric-session-ID parsing defect and timed out without a
catch. Fix `a01fa46e` was deployed at 21:50:54 CST; a single scoped state query
then accounted that original empty catch. It is not a successful end-to-end
fishing acceptance; see below.

Current Lab-only follow-up adds a version-3 settlement resource receipt; this
follow-up is not deployed. Production retains version-2 cast records and the
`a01fa46e` runtime (`810b7eb9` only added the evidence documentation).

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
  or one supply action (added in the supply batch), no automatic mutation retries.
  Request timing excludes local queue/limiter
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
   Include bait/chum consumption mirrors and `result.bonusLoot` in this review;
   the current single-rod projection only commits fish gains and quota. The
   observed missed cast had no bonus loot; do not infer a complete reward path
   from this empty result or assume upstream's fish-only projection covers it.
5. Only then enable integrated-directory routing by default and observe natural
   execution. Retain legacy unresolved recovery; delete old paths in a separate
   cleanup after stability, as requested.

Deferred monitoring debt: the 18:57:59 journal line about an unowned external
Xuangu question timing out caused a generic observer warning. It was not our
identity's failed attempt. No shared observer patch was applied in this batch.

22:20-22:21 observer follow-up: three `pet` retry warning lines were not three
sends. WA actually sent message `1245854` at 22:19:29, then one retry `1245876`
at 22:21:13 in group `-1002083016447`. Reply `1245877` at 22:21:16 confirmed
success, pending cleared, and next pet time became `2026-10-01 00:21:21 CST`.
No reply to the original command was found in the local log. The interim
attempts included a quiet-period block. Warning text emitted before actual
dispatch is a deferred observability debt, not evidence of three game sends or
a reason to modify the shared retry layer during stabilization. 22:48 observer
and watchdog were both ok; no production code/state was changed for this alert.

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

## Controlled Live Evidence

Identity/account/player `7538826434` only; no WA/Baji gameplay, no switch changes.
Original next-fishing time stayed `2026-10-01 00:00:02 CST` throughout.

| Action | Confirmed Result |
| --- | --- |
| Buy spirit-rice bait | 20 received; stones 157015 -> 156315; accounted |
| Buy plain bait | 20 received; 240 stones consumed; accounted |
| Rice chum | 2 plain bait + 30 stones consumed; daily used 1; active for 4 casts; accounted |
| One cast | HTTP 200, but native parser rejected the integer session ID; no hook, checkpoint or fight sent; original cast ID retained |
| Exact cast-ID state read | `missed`, ready=true, caught=false, reason=timeout; quota used 1/5; spirit-rice bait 19; active chum 3 casts |
| Production recovery after `a01fa46e` | One `state` request only; integer session bound, original record `accounted`, quota 1/5, catches `{}`, no pending action; timer unchanged |

Evidence: `/root/xiuxian-native-fishing-canary-state-20260930.json` and sanitized
`data/state/miniapp_capture/fishing-2026-09-30.jsonl`. The read-only tool obtains
the query's cast ID from the matching live identity/account/player record, not a
caller-supplied session or an unscoped context. It does not change the live ledger.

Acceptance fixes now tested:

- Preserve numeric session IDs as bounded positive JSON integers, while retaining
  string compatibility. Reject booleans, floats, zero/negative/oversized IDs and
  cross-type matches. Recovery still queries only the original cast/session.
- Report confirmed supply as success without treating it as a completed rod or
  invoking the daily rod-completion path. Initial canary HTTP 400 responses for
  successful purchases were this UI-status defect, not rejected purchases.
- Broader rollout remains held. The first cast timed out before hook/fight, so
  server acceptance of control proofs remains unverified. Do not spend the
  remaining four casts by automatically retrying this failed acceptance.

## Lab-Only Settlement Follow-Up

- Official controller lines 172-173 defines extra rewards as `bonusLoot` rows
  with `name` and `qty`. The one real missed result explicitly contained `[]`.
- New `parse_settlement_resources` keeps fish, extra gains, bait inventory and
  active/daily chum facts separate. Missing context remains unknown; malformed
  or absent bonus rows cannot silently become zero rewards.
- New casts use a version-3 journal with a bounded settlement-resource field.
  Existing version-2 records remain readable and are not silently upgraded or
  assigned fabricated resource evidence. New resource facts stay pinned to the
  same session/query, and extra gains cannot change on recovery.
- Version-3 projection atomically applies fish plus extra rewards, then scoped
  absolute bait counts (including consumed or rewarded bait), daily reward
  totals and current-day chum state, together with the accounted marker.
  Two fish still count as one rod. UI disabling, changed inventory basis,
  failed saves and old-day counter protection retain their existing boundaries.
- Lab replay of the real read-only result (only its omitted session ID replaced
  by an integer fixture) yields plain bait 18, spirit-rice bait 19, rice chum
  3 casts remaining and daily chum usage 1, with no extra rewards.
- Validation: 276 native tests; expanded suite 2241 passed plus 264 subtests
  (38.65 seconds); compileall and diff checks passed. Not deployed or live-cast
  accepted. Production was still healthy through 22:18, PID `3678694` unchanged.
- Still required: real hook/checkpoint/fight acceptance; scheduler and voyage
  follow-up review, including the native status of configured fish opening and
  gift handling. Do not silently call those original options migrated.

## Verification

### 23:33 Scheduling and Cross-Day Review

- Found native-receipt omissions in legacy startup/reset, status normalization,
  and all-identity daily summary. These paths could change a pending cast's
  projection basis across midnight, or report a day complete before accounting.
- The same unresolved guard now covers both native cast and supply records,
  including malformed receipts. Report acknowledgements also re-check these
  records after notification awaits. Daily quota/done markers no longer hide a
  selected identity's due native recovery; existing backoff and selection remain.
- Focused checks: 202 passed, 17 subtests. Expanded fishing/cave/UI/persistence/
  state checks: 2266 passed, 264 subtests. Compileall and whitespace checks passed.
- Current production remained healthy through 23:32 with the original PID.
  Candidate deployment is still canary-only, not integrated default routing.
- Upstream explicitly defers native auto-open because there is no confirmed
  `/open` contract. Local native gift enqueue and voyage-return handoff remain
  rollout prerequisites, not completed migrations. Do not change voyages or
  reuse old opening endpoints to make the acceptance appear complete.

### 00:04 Controlled Checkpoint Follow-Up (October 1)

- `3c48b6f2` deployed at 23:39:35, PID `3712300`, NRestarts 0, and pushed
  to main. The v3 settlement projection is now live on the canary surface only.
- A second September 30 cast on `7538826434` completed cast/hook, then stopped
  after one HTTP-200 checkpoint. The reply contains the original fighting
  session but no `fight.checkpoint` echo. Local reconciliation incorrectly
  required the echo, so no final fight was sent. Do not call this a catch.
- The official controller's checkpoint handler acknowledges the exact submitted
  duration after its direct successful call without requiring a snapshot echo.
  New reconciliation follows that rule only for the captured direct checkpoint
  response with the same session/site/mode and unchanged challenge. A later
  state query, explicit mismatching checkpoint, foreign owner or stale query
  does not get this allowance. Tests cover repeated checkpoints and final fight.
- One read-only original-session query confirmed `missed/timeout`, quota 2/5,
  bait 18/18, chum remaining 2, and `bonusLoot` of one waterweed ball. One
  recovery invocation then queried that same session and accounted the empty
  rod plus `水草团x1`; no new cast/hook/checkpoint/fight was sent during recovery.
  This verifies v3 extra loot, bait/chum and daily totals, not full fishing.
- Evidence: `/root/xiuxian-native-fishing-canary-v3-20260930.json`,
  `/root/xiuxian-native-fishing-checkpoint-state-20260930.json`, and
  `/root/xiuxian-native-fishing-checkpoint-recovery-20260930.json`.
- Successful timer-neutral canary recovery also clears its current diagnostic
  error when the caller still owns the plan; the original timer is retained.
- Final candidate regression: 2276 passed, 264 subtests; compile and diff checks
  passed. No normal-route enablement or voyage configuration change is included.
- Health observer remained ok through midnight; unrelated cleanup and normal
  native routing are still held. A new single-rod acceptance is required after
  the checkpoint fix, never an automatic retry of the previous failed rod.

### 00:45 Timing Evidence and Lab Hold (October 1)

- `f69f694e` was deployed/pushed at 00:08:00, PID `3721827`, NRestarts 0.
  The next invocation only applied the new day's configured rice chum, costing
  2 plain bait and 30 stones. It did not cast. The following invocation cast
  exactly one rod and received a confirmed `missed/timeout` from `hook`, before
  any checkpoint/fight. It accounted `水草团x1`, quota 1/5, plain bait 16,
  spirit-rice bait 17 and chum remaining 3. No pending native receipt remains.
- This does **not** validate the checkpoint fix against a real complete catch.
  No further casts will be made during this observation window. Retain the four
  remaining daily rods instead of repeating acceptance failures.
- The original-session read confirms biteAt `1790784784356`, expiresAt
  `1790784788356`: a 4000 ms window. The captured hook dispatch was about 2024 ms
  after biteAt, with 3383 ms HTTP elapsed; the response arrived 1407 ms after
  expiry and explicitly reported timeout. These are client dispatch/receive
  times, not a claim to know the exact server arrival time.
- The official browser clock caps RTT compensation at 250 ms. With this cast's
  3262 ms RTT, that leaves a stale estimate on the VPS. Lab now uses the RTT
  midpoint estimate and requires a full observed RTT to fit before expiry;
  otherwise only original-session state reading is allowed. This is an estimate
  under asymmetric latency, not a guarantee against network stalls or early
  arrival, and remains **Lab-only**, not deployed or live accepted.
- Added numeric-only timing fields to the explicit read-only probe; no tokens,
  session IDs or arbitrary server fields are added to this report. Evidence:
  `/root/xiuxian-native-fishing-checkpoint-fix-canary-20261001.json` (supply),
  `/root/xiuxian-native-fishing-checkpoint-fix-rod-20261001.json` (one rod),
  `/root/xiuxian-native-fishing-hook-settlement-20261001.json`, and
  `/root/xiuxian-native-fishing-hook-timing-20261001.json`.
- Lab verification: 137 focused tests; expanded suite 2280 passed plus 264
  subtests, including asymmetric slow transport and late-hook refusal.
  Python compile and whitespace checks passed.
- Live watch: xuruode6 initial-entry timeouts at 00:06/00:08 recovered naturally
  at 00:40:13 with a confirmed eight-hour yuanying departure. No manual resend.
  Other transient reads remain visible. Wisemole's 00:29 deep-start timeout is
  mutation-unknown and must be reconciled by its existing status-first path,
  not a repeated start. Next observation is its 00:59 recheck, then WA's
  01:19 preparation for 01:29 wild training. Other identities are returning real
  wild-training results, not only process heartbeats.

- Initial batch: 117 native tests; 901 fishing tests plus 17 subtests.
- This batch: 194 native tests after unsent-intent compensation.
- Expanded fishing/persistence/state/cave/UI run: 2144 passed, 264 subtests,
  including the final two compensation cases (33.42 seconds).
- Supply batch plus final timer boundary: expanded suite 2200 passed,
  264 subtests (39.13 seconds). Live shop fixture validates without mutations.
- Numeric-session and supply-status fixes: expanded suite 2212 passed,
  264 subtests (39.24 seconds); final read-only scope suite 22 passed.
- 21:54 post-deploy observer ok. PID `3678694`, NRestarts 0. Native capture
  totals: context 4, buy-bait 2, chum 1, cast 1, state 1; all HTTP 200. No hook,
  checkpoint, fight, second cast or forced game-session cancellation was sent.
- Both main and Lab branches were pushed through `a01fa46e`. Background health
  and safety observers remain active. Unrelated debt cleanup has not started.
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
