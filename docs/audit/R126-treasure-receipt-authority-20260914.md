# R126 Treasure Receipt And Quota Authority

Status: scoped offline candidate repaired and verified on 2026-09-15.
Associated regression passes 2867 cases/278 subtests; full regression passes
13510 cases/1275 subtests. Production and whole-project Final Review remain open.

## Required Contract

1. Only the current documented dwelling hunt panel supplies daily quota. Never
   infer a new round from missing, historical, contradictory or malformed
   counts. Valid remaining/limit can derive used, and current quota must not
   regress across one run. An active known session can finish without opening
   another round on missing quota.
2. Selected-player metadata must agree wherever present. The production start
   snapshot must identify the selected account/player. A reveal belongs to the
   current session; settlement must not contradict its request session. An
   omitted response echo is not a fabricated server receipt identifier.
3. A settlement object must contain recognizable current business evidence and
   cannot contradict its completed status. Missing/ambiguous/unowned effects
   remain unknown. Previously confirmed receipts survive a later bad response.
4. Materials come only from current settlement fields. Validate numeric/name
   aliases and booleans; no default quantity for invalid/zero values, no nested
   diagnostic/history gains and no double counting mirrored logs. Sum distinct
   rounds independently. Preserve legitimate current settlement log-only gains.
5. Worker, native consumers and scheduler agree on quota/receipt evidence. Tests
   cover positive multiround and active-session paths, strict adverse inputs,
   partial outcomes and existing lifecycle/cancellation behavior.

## Implemented Scope

- Added `model/features/treasure_receipts.py` for current-panel quota, strict
  nonnegative integral values, explicit session/completion controls and scoped
  settlement materials. Removed the treasure worker's recursive panel search
  and the native inventory source's recursive session search.
- Only `dwelling.hunt` grants new-round authority. The explicit daily-limit
  rejection may use its documented top-level `hunt` panel. Two consistent
  counts can derive the third; absent, boolean, fractional, negative,
  inconsistent and regressing counts cannot open another round. A completed
  entered round whose quota never advances cannot authorize another one.
- A known active run can finish when a partial reply omits quota. Carried
  counts remain display/history context, not fresh opening permission. A
  settled run returned by start is not active and its old result is not a new
  settlement. A plain start/enter daily-limit rejection can establish a stop
  without inventing counts; a 429/5xx response or an active-run contradiction
  cannot. Reveal/settle daily-limit replies do not imply settlement.
- An explicit boolean `foundMain` takes precedence over narrative mentions
  of treasure. False cannot be promoted to found by text, and malformed
  present flags block action rather than becoming truthy or silently false.
  Existing marker/hint target selection remains unchanged.
- Production's selected start requires `account.playerId`; all provided
  account, selector and action player echoes must match. Reveal must retain
  its request session. A settlement may omit an echo, but present session
  aliases and inner/outer completion flags must agree. The worker never
  synthesizes a missing server session ID into a returned receipt.
- Recognizable current settlement evidence is separate from material validity.
  An arbitrary object or explicit noncompletion is unknown, whereas a real
  failed-game/empty-loot settlement is still complete. Invalid material fields
  cannot fabricate a quantity or trigger replay; they are reported as omitted.
- Material extraction only uses direct settlement reward/gain/text fields.
  Numeric/name aliases must agree. Within a receipt, structured values take
  precedence and log/summary mirrors do not add twice; genuine entries inside
  one log are additive. Separate receipts are then summed. Explicit empty
  loot still permits current log-only gains, preserving the 25 + 33 stones
  fixture. Equal structured container mirrors are counted once; conflicting
  same-item container totals are withheld, not guessed additive.
- Native summaries, inventory deltas, captures and result counts use this
  same projection. In-batch duplicate explicit sessions are not counted twice.
  The scheduler only accepts verified quota or an explicit limit rejection as
  daily exhaustion. Old unverified counters may lead to a bounded read, never
  themselves authorize a game mutation.

## Validation Checkpoints

- Original reproduction: **30 failed**;
  `/tmp/xiuxian-r126-repro-20260914.xml`.
- First implementation plus the R125 lifecycle suite: **138 passed**;
  `/tmp/xiuxian-r126-focused-initial-20260914.xml`.
- Nearby legacy suite: **363 passed, 19 subtests, 4 failed**;
  `/tmp/xiuxian-r126-existing-initial-20260914.xml`. Corrected fixtures that
  asserted three settlements from one receipt, lacked a selected-player
  account, or used generic text ratios as opening authority. Gameplay
  assertions remain; fixtures now provide the documented structured fields.
- First associated run: **2855 passed, 278 subtests, 1 failed**;
  `/tmp/xiuxian-r126-related-20260914.xml`. Its replay fixture exposed a real
  compatibility issue with `huntRun.status=settled`; repaired the parser and
  added a start-snapshot regression preventing repeated settlement.
- Expanded focus: **260 passed, 10 subtests**;
  `/tmp/xiuxian-r126-focused-verified-20260914.xml`. This includes **145 new
  receipt cases**, all 108 R125 treasure lifecycle cases and the capture-state
  suite. A transient test-only wrong scheduler helper name was corrected
  before this verification; production logic was not changed to satisfy it.
- A subsequent read-through found narrative override of `foundMain=false`
  and truthiness of malformed flags: **9 failed** before the guard;
  `/tmp/xiuxian-r126-main-flag-repro-20260914.xml`. All nine are included in
  the expanded passing focus.
- Pre-final associated checkpoint: **2858 passed, 278 subtests, 75.51s**;
  `/tmp/xiuxian-r126-related-verified-20260914.xml`. Pre-final full checkpoint:
  **13501 passed, 1275 subtests, 350.66s**;
  `/tmp/xiuxian-r126-full-20260914.xml`. These started before the last
  main-flag repair and do not certify that final code revision. Final reruns
  continue on 2026-09-15; do not substitute these earlier totals.
- Configured Ruff, scoped F checks, compileall, dependency validation and
  whitespace checks pass.
- Final associated rerun on 2026-09-15: **2867 passed, 278 subtests, 75.12s**;
  `/tmp/xiuxian-r126-related-verified-20260915.xml`.
- Final isolated full rerun: **13510 passed, 1275 subtests, 353.30s**;
  `/tmp/xiuxian-r126-full-verified-20260915.xml`. Parsed focused, associated
  and full JUnit reports have zero failures/errors/skips and contain the
  exact same set of all **145 new receipt cases**. External networking was
  disabled; only the isolated loopback was enabled for local HTTP fixtures.
  All test sessions have completed.
- Branch remains `rebuild/stability-20260907`, HEAD
  `cbf152cd61325ca9218c74ee995d643038b20905`. No commit/push occurred. The
  inherited quiz bank and UI-key tool remain unchanged, with SHA-256 values
  `89268c5517eaace4deaefd7e47d17cbc5de1162506e993b7a65a6336784f37ec`
  and `9e72d7ddceea2e03907d2c45c5dd8b3c9078f5c1dd7fd1e8383b4b0937d42b77`.

## Evidence Boundary

The local wxjerry `dwelling_hunt.py` and the Python companion estate module
document `dwelling.hunt`, `huntRun` and `huntResult`, selected `playerId`,
same-session reveal/settle, and quota-based continuation. They are references,
not current production evidence. Do not copy their loose quantity defaults,
counter inference or uncertain-action reopening.

Historical/cross-day receipt identity, atomic inventory accounting and a durable
per-dispatch journal remain separate work. Current-result source projection
does not migrate old digest-based inventory keys or prove historical replay
identity. No game HTTP, production files,
configuration/database changes, service/listener operations, deployment,
commit/push, skill edits or World Boss/refinement changes are authorized here.
R67 subordinate cultivation remains unvalidated in production.
