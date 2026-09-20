# Tianjige / Pavilion Migration Checkpoint

Date: 2026-09-20. Base: `1b6be371`.
Worktree: `/root/xiuxian-tianjige-pavilion-20260919`.
Branch: `fix/tianjige-pavilion-20260919`.

## Deployment Boundary

Candidate code only. No production deployment, restart, account enablement,
or live gameplay mutation was performed for this checkpoint. Production
`/opt/xiuxian-main` remains separate. Existing business/UI switches are retained.
This is not a claim that all companion commands have migrated.

## Verified Server Contract

- Public-entry `commandCenter` now exposes Tianxing commands as integrated.
  A read-only channel probe for identity `3765328695` returned a complete panel
  for the selected channel player, despite group send-as being closed.
- The official dwelling frontend renders companions under `pavilion` (Bao Ge).
  Its buttons call `command-center`; this is not a new companion-mutation API.
- A single live read for account `301299112` returned HTTP 200 and
  `actionResult.ok=true/completed=true` for `.我的侍妾`. The current partner was
  南宫婉·月影, with dream/heart/divination cooldowns of 472/592/712 minutes at
  the time of the read. These are historical observations, not fixed timers.
- The red-dust panel omits affinity. Missing affinity must not erase a known
  value for the same partner. The numbered/Markdown panel is replay-tested.
- The `section=pavilion` probe returned no companion at `account.dwelling`;
  that earlier probe inspected the wrong nesting level. A corrected one-shot
  read on 2026-09-20 used top-level `dwelling`, matching the official frontend,
  and found the selected companion with `active`, `isStarPalace`,
  `greetedToday`, and exact `raw.affection`. The sample was 南宫婉·月影,
  affinity 1274, `greetedToday=false`; it is a red-dust companion and therefore
  not eligible for the Star Palace daily-greeting automation.
- A second bounded read-only schema probe confirmed the fragment fields under
  the unique active companion's `raw` object. Virtual Heaven and Cangkun bags
  are fixed four-key non-negative integer maps; puzzle completion counters are
  integers; dream and per-route puzzle times are timezone-qualified ISO values.
  Fragment progress counts distinct present pieces, not duplicate quantities.
- Heart-trial initiation and oath confirmation still require group replies in
  the official UI. An HTTP panel must never manufacture a Telegram anchor.

Local evidence (no auth material):

- `/root/xiuxian-channel-tianji-probe-20260919.json`
- `/root/xiuxian-pavilion-readonly-20260919.json`
- `/root/xiuxian-pavilion-fields-20260919.json`
- `/root/xiuxian-pavilion-fragments-20260920.json`
- `/root/xiuxian-pavilion-fragments-times-20260920.json`
- Official page inspected at `/tmp/xiuxian-dwelling-20260919.html`.

## Implemented Candidate

1. Exact whitelist for Tianxing panel, observe, star selection, prediction,
   change-fate and calamity clearing. Only enumerated stars/routes are accepted.
2. Selected-player public-entry transport with identity/account ownership,
   operation checks before/after awaits, per-identity locking, existing global
   HTTP rate limiting, and no command POST retries or post-dispatch fallback.
   Only session-establishment token failures can try another configured entry.
3. Separate HTTP receipts for Tianxing timeline, auto planner and manual UI
   actions. Group reply-binding rules remain unchanged. No synthetic message IDs.
4. Frozen channel wild-training preparation can use the public entry, without
   enabling group sends or changing cautious/balanced/deep strategy or quota.
5. Timeline unknowns calibrate through a fresh panel. Automatic prediction,
   change-fate and star-selection unknowns can also be cleared by a fresh panel
   proving the requested state, but not by missing/expired/different effects.
6. Automatic companion status uses the pavilion's command-center route, except
   when a group panel is needed for a due heart trial. Existing snapshot reducer,
   stale/pending guards, switches and cooldown logic are reused.
7. Voyage status, launch and return use the public-entry transport. Status reads
   apply the existing voyage parser under owner/plan guards and never submit a
   settlement request. Launch/return retain the existing owned records and reducers with
   explicit HTTP receipt validation. Confirmed return rewards use the existing
   audit path. Unknowns survive restart and are never replayed as group commands.
   Unknown launch/return now performs an owned `.远航状态` read after at least
   30 minutes, with a persisted 30-minute read-backoff and no mutation replay.
   Matching-route sailing/returned state reconciles launch; explicit no-task
   state reconciles return. Ambiguous, wrong-route or wrong-partner panels do not.
   The separate `reconciled` record preserves the probe receipt and never claims
   the original reward or affinity delta. Return reconciliation requires a later
   panel with explicit affinity before the old balance can authorize new work.
8. Regression checks cover stale owners/plans, frozen vs manually disabled
   identities, malformed receipts, unknown outcomes, failed saves, SQLite reload,
   scheduler recovery, cancellation, reducer rollback, read throttling,
   forged reconciliation records and the real red-dust panel.
   Tests use isolated temporary databases.
9. `.天机代卜` now has candidate public-entry transport. The official pavilion
   button uses that exact command. Existing switches, admission, resource
   shortage handling and effect/cooldown reducer remain authoritative.
   Mutation intent precedes dispatch; explicit HTTP receipts replace no group
   anchors and uncertain requests never fall back to a group send.
   After at least 30 minutes an owned `.我的侍妾` probe may reconcile a complete
   same-partner panel with explicit chain state and a new future divination CD.
   Ready/expired/partial/foreign panels cannot release an unknown mutation.
   Reconciliation records no inferred spend and preserves the cooldown as a
   send floor even if the runtime cache is cleared.
10. Star Palace `.每日问安` now has candidate public-entry transport. Existing
    daily, sect, affinity, partner and module switches remain authoritative.
    A complete command receipt reuses the native success/already-done reducer.
    An uncertain command is never replayed. After at least 30 minutes the owned
    `section=pavilion` read may close it only when the unique active Star Palace
    partner matches. On the same game day `greetedToday` must be true; false is
    inconclusive and receives another 30-minute read backoff. After the day
    changes, the exact structured affinity closes the old operation without
    repeating it, while current-day `greetedToday=true` also blocks a duplicate.
    The evidence uses a separate HTTP receipt and no synthetic Telegram IDs.
11. `.残图`, `.入梦寻图` and `.拼图` now have candidate Pavilion transport.
    `.残图` is a single structured `section=pavilion` read and persists a fresh,
    owner-bound HTTP confirmation with no synthetic Telegram message anchor.
    `.拼图` still requires that complete confirmation within the original reuse
    window. Dream and puzzle mutations persist intent before dispatch, reuse the
    native parsers/reducers for complete command results, and never retry or
    fall back to group commands after an uncertain POST. After at least 30
    minutes, a structured Pavilion read may reconcile an unknown only when the
    matching server event timestamp is newer than that operation. Ambiguous or
    old snapshots remain unknown with another 30-minute read backoff. No reward,
    resource spend or Telegram reply is inferred.

## Open Release Gates

- Complete a single-account live **mutation** canary before enabling candidate
  transport for all configured identities. The live probes above were reads,
  not mutation or end-to-end eight-run wild-training acceptance.
- Verify the live voyage panel wording during the single-account canary.
  Missing launch evidence still retains the unknown operation. For a lost return
  receipt, a red-dust panel without affinity cannot confirm the new balance;
  a fresh explicit balance or manual reconciliation is still required. Do not
  invent rewards or reset the known affinity to zero to force progress.
- Gifting remains on its original group transport. The official command syntax,
  item availability and manual-only inventory boundary require a separate review.
- Explicit no-partner status is handled only from the module-owned automatic
  read; the existing manual UI read remains observation-only.
- No automatic inventory reads were introduced. Gift inventory handling is out
  of scope for this candidate.
- Do not migrate `.共历心劫` or oath replies through this HTTP command adapter.
- CommandAttempt remains unchanged; no new retry/recovery control was assigned
  to its shadow ledger.
- Pavilion transport has a separate, default-off release gate:
  `cave_public_concubine_enabled` plus
  `cave_public_concubine_identity_ids`. It only chooses transport for new
  companion operations; existing companion business switches remain the
  scheduler authority.
- Revoking the Pavilion gate before dispatch cancels that HTTP dispatch. An
  already-unknown MiniApp mutation may still use an owned read-only Pavilion
  snapshot for reconciliation, and is never replayed through a group command.
- The MiniApp UI exposes the master switch and per-identity Pavilion allowlist.
  Production can therefore start with exactly one canary identity without
  switching every eligible companion identity.

## Verification

Focused command (run from this worktree):

```sh
/opt/xiuxian-main/.venv/bin/python -m pytest -q \
  tests/test_tianjige_transport.py tests/test_tianxing.py \
  tests/test_wild_training.py tests/test_wild_training_lifecycle.py \
  tests/test_cave_command_identity.py tests/test_cave_tianjige_read_only.py \
  tests/test_cave_read_only_lifecycle.py tests/test_concubine*.py
```

Previous expanded regression: 4381 passed, 119 subtests passed.
Voyage actions plus HTTP reconciliation after this change: 285 passed.
Expanded regression after voyage reconciliation: 4413 passed, 119 subtests passed.
Initial divination HTTP and native lifecycle tests: 167 passed. Additional
cooldown-floor, reload, stale-receipt and reducer-rollback tests are included in
the final expanded regression.
Final expanded regression including divination: 4467 passed, 119 subtests
passed (55.36 seconds).
Greeting HTTP/structured-pavilion protocol and native lifecycle tests: 202
passed.
Fragment transport, structured-read and native lifecycle focused tests: 496
passed. Final expanded candidate regression: 4578 passed, 119 subtests passed
(56.06 seconds).
Release-gate, allowlist, UI-contract and Pavilion focused tests after the
canary guard: 332 passed. Expanded candidate regression after the guard,
including MiniApp UI/config tests: 4029 passed, 9 subtests passed (29.08
seconds); this is not a production gameplay acceptance.
`git diff --check` and changed-module compilation passed.
Production checkout remains clean; service is active/running with
`NRestarts=0` and PID `3380056` at this checkpoint. This is process evidence,
not live gameplay acceptance. Candidate changes remain uncommitted.
