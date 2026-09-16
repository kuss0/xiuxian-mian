# R75 Small-World Selected-Player Contract

## Scope and Evidence

This is an offline candidate change in `rebuild/stability-20260907`, not a
production rollout. It follows the user's report about Jfdffdddd's subordinate
MiniApps and the confirmed R67 retreat identity defect.

`run_cave_small_world_production_flow` accepted a selected `player_id` and used
it for the initial dwelling read, but omitted it from the actual small-world
mutation. The planner's arbitrary payload could also overwrite action, player,
or authentication fields. Missing or selector-only account metadata could be
accepted as success. Even a rejected foreign account's result text was exposed
to the runtime, where resource wording and wait durations affected scheduling.

Read-only inspection of the retained September 11 capture found two `collect`
requests, for primary accounts `8659059191` and `301299112`. Both sent only
`action`, `initData`, and `token`. Their response shapes contain an integer
`account.playerId`, boolean `actionResult.ok`/`completed`, and partial snapshot
metadata. Shapes do not establish actual balances or the values of those
booleans. These are primary-account captures, not proof of live channel
small-world spending. The channel defect is reproduced offline through the
real selected-entry and action adapters.

## Candidate Contract

- The builder requires an explicit, valid `player_id`. The flow validates it
  against the intended role before authentication or HTTP. Preserve the
  server-selected signed ID in both the read and mutation requests.
- Planner metadata cannot override `action`, `playerId`, `token`, or
  `initData`. Ordinary action metadata, including a refinement amount, remains
  supported. This does not enable refinement or any new gameplay action.
- Before planning from an initial snapshot, require an explicit matching
  `account.playerId`. Use the R67 validator: bools, floats, containers, absent
  IDs, foreign accounts, and contradictory selector/account metadata fail.
  A selector echo alone is not action ownership. Equivalent normalized and
  signed representations of the same role remain valid.
- Validate action ownership before exposing its business receipt or resource
  snapshot. Unknown/foreign receipts cannot become a success, resource block,
  or business cooldown. Keep dispatch and HTTP/Retry-After evidence, and make
  no automatic second mutation request.
- A valid receipt can confirm an action without a full resource panel.
  Retain that confirmation through later panel parse failure or cancellation;
  never relabel the pre-action balances as a fresh post-action snapshot.
- `ok` and an explicitly supplied `completed` must be boolean true for
  confirmation. A missing `completed` remains compatible with older fixtures;
  zero, null, and string false are not completion evidence.

The runtime's existing owner/switch/schedule revalidation, bounded thread
draining, 8-hour harvest, 6-hour checks, and disabled automatic incense
refinement remain unchanged. World Boss and Tianxing behavior are untouched.

## Verification

- Initial identity reproducer: **60 failed**, 73 deselected. Artifact:
  `/tmp/xiuxian-r75-identity-before-20260911.xml`.
- Identity patch plus related lifecycle/protocol/runtime/retreat suites:
  **414 passed, 68 subtests**, 20.65 seconds. Artifact:
  `/tmp/xiuxian-r75-identity-after-20260911.xml`.
- Follow-up malformed completion reproducer: **3 failed, 5 passed**. Artifact:
  `/tmp/xiuxian-r75-receipt-before-20260911.xml`.
- Final coverage includes all ten whitelisted action builders/transports,
  signed/normalized response IDs, frozen-channel selection, unchanged primary
  state, foreign wait-text rejection, cancellation, partial panels and HTTP
  backoff. Existing partial-response fixtures now include the account field
  shown in native captures; identity validation was not weakened to keep them
  green.
- Full candidate suite in a network namespace with only loopback enabled:
  **7932 passed, 1275 subtests**, 141.32 seconds, exit 0. Artifact:
  `/tmp/xiuxian-r75-identity-full-20260911.xml`.
- Broad Ruff, compileall, dependency consistency, JS syntax, and diff checks
  pass. Tests use temporary data and `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

## Remaining Boundaries

This closes the reproduced small-world request/receipt identity defects in the
candidate, not whole-project acceptance. General unknown-action recovery after
forced stop, legacy resource chronology, other MiniApp workers, R65 migration
and capacity, R74, and separately gated R07 work remain open.

Production files, services, switches, database and timers were not changed.
No game request, listener activation, commit, push, or skill edit occurred.
R67's existing channel retreat projections remain unverified until separately
authorized rollout and identity-bound rebaseline. Do not replay mutations or
reset timers merely because candidate tests pass.
