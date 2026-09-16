# R94 Concubine Heart-Trial Lifecycle

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production files, services, settings, database,
game requests, listeners, skills, commits, pushes or deployments are changed.
World Boss/refinement stay disabled, inventory API stays explicit UI-only and
CommandAttempt stays shadow-only. Deep retreat never consumes or blocks
Tianxing effects. Whole-project Final Review is still open.

## Finding And Evidence

The former heart trial had scalar launch/choice anchors, untracked sends,
guessed cooldowns after uncertainty and weak log/passive completion. R93's
text parser did not fix operation ownership, persistence or orchestration.

Initial lifecycle reproducer: 11 failed, 1 passed.
/tmp/xiuxian-r94-heart-reproduced-20260912.xml.

Review reproduced nine more failures with 207 passing controls: malformed
persisted phases, late rounds while a read probe is still open, outer-scan
failure invalidating the saved plan, and legacy timeout-written heart CD.
/tmp/xiuxian-r94-heart-review-reproduced-20260912.xml.

Final contract review reproduced five failures with 230 controls: passive
event ID coercion and a terminal receipt marked as an unfinished session.
/tmp/xiuxian-r94-heart-last-review-reproduced-20260912.xml.
These are offline regressions, not counts of observed production incidents.

## Owned Lifecycle

- One persisted concubine_heart_session per identity: launch plus at most three
  sequential choices, one replaceable read-only probe and at most three probes.
  The slot is registered in runtime/JSON state and both SQLite declarations.
- Save intent before tracked, zero-retry transport. Capture identity object,
  account, original chat, session/operation, partner snapshot and current plan.
  Recheck the exact owner, switches, plan and sibling work at queue dispatch.
- A fresh definitely-unsent receipt permits bounded backoff and another attempt.
  Unknown sends, exceptions and cancellation retain ownership; neither timeout
  nor startup invents a business cooldown or authorizes mutation replay.
- A launch needs a fresh completed owned status query. Arbitrary cached/logged
  panels cannot become reply targets. Cross-identity five-minute launch spacing
  uses owned sessions rather than unowned command-text matches.
- Each round binds the preceding owned bot prompt. Telegram edits may retain
  the same, older prompt ID, but must come from the same official sender with
  a later authoritative event time and the expected sequential round. New
  prompts need the original owned reply chain. Ambiguous owners/receipts and
  conflicting route metadata cannot authorize a result.
- Native and passive handlers share the reducer before generic deduplication.
  Strict IDs and server clocks survive the passive boundary. Unowned scalar
  settlements and guessing a sole active identity no longer mutate state.
- Terminal facts and projections commit together. Duplicate settlement cannot
  apply affinity twice; newer partner/affinity snapshots and replacement work
  are preserved. Cultivation/demon facts are retained evidence, not additional
  accounting writers. Exact original command/chat cleanup closes only its guard.
  Failed guard cleanup retries cleanup without undoing the committed result.

## Recovery And Scheduling

- Replay only owned receipts and trusted message/edit logs. Same-time conflicting
  revisions do not select an arbitrary winner. Unknown spending stays unresolved
  when evidence is absent; legacy scalar phases are not silently reset.
- Anchor loss/in-progress may start bounded status reconciliation through
  CMD_CONCUBINE_STATUS. A positive authoritative CD can
  close an unknown operation without inventing rewards. A ready panel closes
  only explicitly lost anchors, not an unexplained send or in-progress trial.
- Probes expire as reads, with exact pending cleanup and transactional saves.
  Mutations do not expire under this rule. A late round waits for an outstanding
  probe to answer/expire, then continues the same session and original prompt.
- Delayed choices bind owner, session, operation and plan across sleep. The
  ordinary outer scheduler reaches recovery before its scalar timeout check;
  a paused round can resume after re-enable without waiting for the old timer.
  Outer-scan failures only annotate the owned heart error, not rewrite its plan.
- Completed replies still process while paused or after the default group
  changes. They do not launch new work or overwrite a replacement plan. A
  changed business plan must be reconciled rather than silently rebased.
- Remove legacy heart retry/replay/restore helpers and timeout-written heart
  cooldowns. Retained non-heart legacy paths are not certified by this change.

## Verification

- 235 lifecycle cases, including native/passive routes, real runtime receipts,
  temporary SQLite reload, actual log replay, ownership changes, ambiguity,
  pause, queue rechecks, malformed state, save failures and scheduler integration.
- Replaced 36 obsolete scalar/guessed-CD heart tests and six old affinity
  projection cases with owned lifecycle coverage; R93 parser tests remain.
  Removed tests asserted behavior that the approved ownership contract forbids,
  rather than supplying trustworthy end-to-end evidence.
- Focused final: 2349 passed, 24 subtests, 19.36s.
  /tmp/xiuxian-r94-focused-final-20260912.xml.
- First full run: two obsolete outer integration assertions failed, with
  10589 passed and 1275 subtests. Update the startup test to preserve the whole
  unowned heart state without scheduling work, and the public-entry test to
  expect the scoped heart-session rejection while still asserting no I/O.
  /tmp/xiuxian-r94-full-first-20260912.xml.
- Outer integration after those assertion migrations: 401 passed, 5 subtests,
  4.56s. /tmp/xiuxian-r94-outer-integration-final-20260912.xml.
- Final isolated full: 10591 passed, 1275 subtests, 163.91s, exit code 0.
  /tmp/xiuxian-r94-full-final-20260912.xml.
- Configured/fatal Ruff, compileall, pip check and git diff --check pass.
  Tests use temporary SQLite with XIUXIAN_ALLOW_LIVE_TEST_DB=0. Full-suite
  networking is isolated with only loopback enabled. All test sessions ended.

## Remaining Gates

Reacquisition and legacy/manual reconciliation remain open, as do R65/R74
accounting/capacity, shared R07 forced-stop durability, rollout/rollback and
whole-project Final Review. A newly persisted unknown heart session must not be
ignored by an old reader; code-only rollback must not revive the old retry/CD
logic. Exhausted or invalid records require evidence reconciliation, not deletion.

R67's production jfdffdddd subordinate-role MiniApp cultivation has not been
revalidated or resumed by this work. Its candidate selected-player fixes still
need a separately authorized rollout and identity-bound production baselines.
