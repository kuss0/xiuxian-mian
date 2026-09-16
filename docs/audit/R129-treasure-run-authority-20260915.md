# R129 Treasure Active-Run Authority And Progress

Status: scoped implementation, fault tests and final offline verification
complete. Not approved for rollout; the whole-project objective remains open.

## Required Contract

1. Native search uses explicit current `huntRun` cells, exact zero-based wire
   indices and literal revealed flags. Missing/invalid/duplicate cells cannot
   turn into guessed indices. Sparse explicit indices are not clamped to a
   guessed cell count or overridden by legacy aliases.
2. Search requires valid current AP/status and consistent provided geometry
   and AP limits. Hints can rank selectable cells, but cannot create them.
3. An explicit main find, failed run, exhausted AP or fully observed exhausted
   board permits original-session settlement without requiring an unused
   search target. Narrative alone does not prove a native main find.
4. A reveal reply must advance that session's selected cell or an explicit
   terminal outcome. Unchanged, contradictory or regressed state retains the
   exact pending request and prevents further mutations.
5. Checkpoint admission applies the same selectable-cell and progress rules.
   Results/receipts from earlier confirmed settlements remain preserved;
   no mutation retry, new endpoint, production change or extra budget is added.
6. Same-session partial replies retain earlier revealed cells and geometry.
   Previously revealed cells cannot become selectable again. A confirmed
   reveal need not offer another target; an accumulated complete exhausted
   board may settle, while an incomplete board without a target stays held.

## Implementation And Review

- `treasure_runs.py` owns native run parsing, action eligibility, reveal
  progress and retained same-session board evidence. Actual `cells[].index`
  values and literal `revealed` booleans replace guessed counts and index
  clamping. Explicit geometry, AP bounds and duplicate indices are checked.
- Hints rank only currently selectable cells. Malformed optional marker
  containers are ignored and marker iteration is bounded. Native narrative
  alone does not authorize a main-find settlement.
- The worker validates response progress before resolving its exact pending
  request. Unchanged replies, lost targets, conflicting geometry and reverted
  revealed cells retain the request and cannot trigger another mutation.
- Checkpoints retain verified run/board flags, geometry and revealed targets;
  validation enforces disjoint, bounded indices and prevents a later frame
  from discarding earlier revealed cells. SQLite reload preserves the hold.
- Final review exposed seven more failures: numeric-string AP comparison
  raised TypeError; a fully exhausted board unnecessarily required search AP;
  and a partial last-cell response could not complete the retained board.
  Search AP is compared after the same strict conversion. Full board evidence
  can authorize settlement independently of search AP. A known reveal without
  a next target now closes that request without guessing another cell; any
  remaining original-round work is still explicit, not declared complete.
- The worker already retains known geometry, but a forged subsequent
  checkpoint could erase it while preserving current targets. A reproducer
  confirmed this separate admission gap; checkpoints now retain an earlier
  nonzero board size as well as revealed cells. Two unused lines in the
  touched WebApp test file were removed; no assertions were weakened.

## Verification

- Original reproduction: **39 failed, 3 passed**;
  `/tmp/xiuxian-r129-repro-20260915.xml`.
- The first repair had **523 passed, 1 failed**. The failed legacy fixture
  relied on narrative alone; it now supplies native `foundMain: True`. Two
  WebApp fixtures now supply explicit cells instead of AP-derived targets.
- Expanded verification initially passed **916 cases and 19 subtests**.
  Further partial-response controls reproduced **3 failures**;
  `/tmp/xiuxian-r129-partial-repro-20260915.xml`. The resulting focused suite
  passed **949 cases and 19 subtests** before final board-completion review.
- Board-completion reproduction: **7 failed**;
  `/tmp/xiuxian-r129-board-completion-repro-20260915.xml`. All **82 cases**
  then passed, including retained last-cell completion through the real writer,
  no-progress native callers, foreign/regressed boards and SQLite reload.
  `/tmp/xiuxian-r129-controls-reviewed-20260915.xml`.
- Pre-geometry focused suite: **956 passed, 19 subtests**, 57.90s;
  `/tmp/xiuxian-r129-focused-final-20260915.xml`.
- Initial full suite: **13921 passed, 1275 subtests**, 374.92s;
  `/tmp/xiuxian-r129-full-final-20260915.xml`. This precedes the last
  checkpoint-geometry repair and is not its final verification.
- The checkpoint-geometry reproducer had **1 failed, 1 passed**;
  `/tmp/xiuxian-r129-checkpoint-geometry-repro-20260915.xml`. All **83 new
  cases** now pass; `/tmp/xiuxian-r129-controls-final-20260915.xml`.
- Final focused suite: **957 passed, 19 subtests**, 58.55s;
  `/tmp/xiuxian-r129-focused-reviewed-20260915.xml`. Final associated suite:
  **3500 passed, 307 subtests**, 101.95s;
  `/tmp/xiuxian-r129-related-reviewed-20260915.xml`.
- Final full suite: **13922 passed, 1275 subtests**, 373.69s;
  `/tmp/xiuxian-r129-full-reviewed-20260915.xml`.
- The three final XML reports contain identical sets of all **83 new cases**,
  zero failures/errors/skips and successful exits. All test sessions ended.
  Tests ran with `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and isolated networking;
  loopback was enabled only for local HTTP smoke tests.
- Configured `ruff check model tests`, scoped full `F` checks on the treasure
  modules and their associated tests, `compileall`, `pip check`, tracked diff
  whitespace and selected new-file whitespace checks pass. The inherited
  quiz-bank/UI-key tool hashes and candidate HEAD remain unchanged. This is
  offline evidence, not production acceptance.

## Recovery Dependency

The local wxjerry and companion references describe the existing read-only
`start` endpoint plus same-session hunt/reveal/settle. They are protocol
references, not new game evidence. R128 retains only a session digest, and its
completed local projection is linked to the original checkpoint. Original-round
recovery must first validate the observed board and must later reconcile that
link without duplicating already projected material. In particular a pending
enter has no returned round ID; a fresh active board alone cannot identify it.

This step repairs the current action authority needed by both ordinary play
and eventual original-round reconciliation. It does not declare R128's unknown
or original-round holds resolved. Original-round reconciliation, historical
identity/migration, retention/delivery and whole-project Final Review remain
required work. R67 subordinate-role cultivation is still unvalidated in
production.

No production/configuration/DB changes, game probes, listeners/services,
deployment, commit/push or skill edits. Preserve World Boss/refinement switches,
inventory API UI-only use, CommandAttempt shadow-only and Tianxing/deep-retreat
independence.
