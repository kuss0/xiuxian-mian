# R83 Concubine Gift Lifecycle

Date: 2026-09-11.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
Base HEAD: cbf152cd61325ca9218c74ee995d643038b20905.

This is an offline candidate repair of gift inventory reads and gift spending.
It does not close the entire concubine module or the whole-project goal.
No production service, database, configuration, game traffic, remote, skill,
listener or user-owned quiz/tool changes were modified. World Boss and
refinement disablement and the CommandAttempt shadow-only boundary remain.

## Evidence

The initial 18 cases failed: the old sends recorded ownership after awaiting
transport, wrote through removed/replaced/rebound owners, cleared unknown
work on startup and dispatched gifts without an owned inventory observation.

Routing review then reproduced 17 failures, including two integration bugs:
the passive inventory handler saved a bag and returned before the gift
reducer, and text dedupe prevented retrying a failed local completion save.
Contradictory identity/account/chat/root/command metadata was also accepted.
Final review reproduced old inventory reply fallback, pre-send save exception
misclassification, and daily-marker reset bypasses; those cases are repaired.

The first full run passed 9075 tests and 1275 subtests with five failures:
new generic gift callbacks demanded sender metadata from unrelated event
fixtures. The generic callbacks were removed. Only owned gift routes use the
new metadata contract; the unrelated second-soul/storage fixtures were not
weakened to satisfy it.

## Ownership And Dispatch

The bounded concubine_gift_actions JSON field stores at most one gift_bag and
one gift record per identity. Both retain an operation ID, identity/account,
chat, command, dispatch/receipt clocks, module-plan fingerprint and status.
Gift records also retain the exact preceding bag operation, amount, partner,
affinity baseline, day and inventory fingerprint. The field is registered in
defaults, runtime columns, JSON codec and both additive/new SQLite schemas.

Pending intent saves before transport. Each send explicitly supplies identity,
chat and operation ID, tracks its receipt, uses max_retry=0 and preserves the
900-second reply timeout and existing fast-scan queue timeout override.
Queue admission revalidates owner, controls, day, plan, inventory and the
phaseful-summary guard in the captured identity context. An in-flight token
prevents reentry. Early completion wins over a delayed receipt or exception.

Only a fresh, explicitly unsent block permits a scheduled retry. An absent or
stale block, cancellation, exception or invalid receipt retains unknown work.
Failure to save the intent prevents dispatch. Unknown gifts do not expire or
retry on a later day. Unknown bag reads may expire after exact recovery and
900 seconds; this is never permission to repeat a resource mutation.

Gift spending requires the preceding complete, recent, same-day, owned bag
read and the unchanged partner/affinity plan. A generic/manual inventory
snapshot, daily-attempt marker or scalar phase is not authorization. A saved
ready bag can continue after interruption while its plan is still valid.
Confirmed daily facts also prevent repetition if UI scalar markers are reset.

## Replies And Recovery

Native and passive owned replies use the same reducer, before generic
consumed-message cleanup, passive text dedupe or inventory snapshot fallback.
They require an official bot, exact command root/chat and server observation
clock. Explicit conflicting account, identity, root or edited-command metadata
is rejected. Missing account metadata in actual runtime receipts is accepted
only through the original unique operation ID and unchanged owner account.

The shared exact receipt helper is reused with a source-module parameter.
Reply-route admission does not scan message logs. Recovery uses the original
and recent bounded 20-minute windows at most once per minute. Latest edits
supersede earlier versions of the same message, and equal-clock conflicts do
not expose older complete text. Recovery invokes the reducer, not just receipt
registration. Retained older bag roots cannot become new passive snapshots.

Incomplete, unrelated or unowned inventory panels do not permit spending.
Gift success must match the requested amount and saved partner. Unknown text
does not consume a daily success or discard pending work. Explicit shortage,
no-partner and daily-limit results close the operation conservatively.
Only success/explicit daily completion updates last_gift_day; known failed
attempts can suppress further gifting that day without fabricating success.
The operation day, not delayed local delivery, owns the daily marker.

Completion, exact pending cleanup and local numeric effects save without an
await before any continuation. A failed/throwing completion save restores
the pending operation and inventory and withholds the next send. Replaying
that reply can finish later without sending another command. Cleanup cannot
delete a replacement operation at the same root.

Confirmed gift gains apply once to an unchanged saved affinity baseline.
Inventory debits apply once only if the dispatch-time inventory fingerprint
is still current and its observation precedes dispatch. Newer/changed local
snapshots are not blindly adjusted again. The confirmed result remains in
the record even when a newer snapshot prevents a scalar projection. Disabling
a module after dispatch does not discard a confirmed result or permit stale
callbacks to rewrite new schedules or controls.

## Verification

- New lifecycle cases: 139.
- Final focused suite: 953 passed, 24 subtests, 7.72s.
- Focused artifact:
  /tmp/xiuxian-r83-gift-verified-focused-20260911.xml.
- Initial full artifact:
  /tmp/xiuxian-r83-gift-full-20260911.xml.
- Second isolated full run: 9083 passed, 1275 subtests, 147.11s.
  /tmp/xiuxian-r83-gift-verified-full-20260911.xml.
- Configured Ruff, E9/F63/F7/F82, compileall, pip check and diff checks pass.
- All test sessions completed. Only documentation changed after the second
  full run; no runtime/test changes followed that verification.
- Dedicated candidate venv and temporary SQLite data only. Full-suite runs
  use separate network namespaces with only loopback enabled.

Additional failing replay artifacts:

- /tmp/xiuxian-r83-gift-before-20260911.xml
- /tmp/xiuxian-r83-gift-routing-before-20260911.xml
- /tmp/xiuxian-r83-gift-final-review-before-20260911.xml
- /tmp/xiuxian-r83-gift-day-reset-before-20260911.xml

## Remaining Gates

- Follow-up R84-concubine-greet-lifecycle-20260911.md moves the implementation
  to concubine_affinity_actions.py and reuses it for daily greeting ownership.
  The gift JSON schema and source tags are preserved; all R83 cases remain.
- Legacy scalar-only gift pending is held, not erased, retried or migrated
  into invented operation IDs. Explicit migration/reconciliation is required
  before any rollout. This is a safety hold, not a claim of legacy liveness.
- R84 covers the scoped greeting follow-up. Dream, puzzle, divination, heart
  and voyage lifecycles still need review. R83 does not apply its read
  expiration to those mutations.
- A two-record lifecycle is not a complete accounting/history ledger.
  Broader manual/unthreaded effects, field chronology, overlapping inventory
  changes and post-terminal corrections remain open. Skipping a stale scalar
  projection does not establish the current authoritative resource balance.
- Shared forced-stop/save-failure durability and malformed raw JSON decoding
  remain R07/persistence work. These tests do not authorize a new shared
  recovery controller or CommandAttempt control.
- A future deployment needs snapshot/schema/rollback and legacy-pending
  migration review. Old code must not ignore this column and resume gifting.
- R65/R74 migration/capacity, the remaining matrix and whole-project Final
  Review remain open. Production jfdffdddd subordinate-role MiniApp
  cultivation is still unverified; no online recovery is claimed here.
