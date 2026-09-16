# R97 Wanxin Owner Reply Contract

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production code, state, switches, services, game
requests, listeners, skills, commits, pushes or deployments are changed.
World Boss/refinement stay disabled, inventory API stays explicit UI-only,
CommandAttempt stays shadow-only, and deep retreat never affects Tianxing.
R67 production subordinate-role cultivation remains unvalidated.

## Reproduction

Owned Wanxin replies could be correctly routed yet incorrectly settled:

- An owner-action or status title alone counted as success/completed reading.
  Missing greeting gains or seal costs silently became zero. Failed/incomplete
  visit and protection text also passed broad substring checks.
- Numeric prefixes, duplicate fields and incomplete absolute panels were
  accepted. A moon title implied awakening even without the corresponding
  partner, resonance or absolute affinity.
- A heading overrode explicit cooldown/refusal text. A mixed cooldown selected
  the first named action, and instructional/negated already-done phrases
  consumed the daily action.
- A later partial or type-changing edit of a completed greeting replaced its
  accounted delta checkpoint. A subsequent complete edit could then repeat the
  affinity gain or rebase the cooldown.

Offline reproductions, not production incident counts:

- Initial: 75 failed, 5 passed.
  /tmp/xiuxian-r97-reproducer-20260912.xml
- Refusal/context review: 11 failed, 95 passed.
  /tmp/xiuxian-r97-contract-review-20260912.xml
- Legacy-heading/expense review: 6 failed, 106 passed.
  /tmp/xiuxian-r97-header-review-20260912.xml

The first implementation also rejected complete protection/deduction receipts
because they contain both deltas and absolute totals. Real-fixture regression
caught three failed tests and two failed subtests. The parser now keeps these
two numeric roles separate; the original supported fixtures are unchanged.

## Contract

- Parse at most 4096 characters and normalize line endings. Multiple declared
  result headings are ambiguous, not first-heading-wins.
- An absolute stat panel must contain all four unique fields. Match whole
  fields at line/pipe boundaries, not numbers embedded in narrative text.
  Missing/partial, duplicate, signed absolute, decimal, non-ASCII numeric and
  overflowing values cannot calibrate state. Optional stages are bounded and
  unique per stage label; the existing stage precedence is preserved.
- Action deltas and absolute panel totals are distinct. Greeting gain, seal
  expense and deduction gain need their explicit complete integer facts.
  Explicit zero is valid; absence is not zero. Numeric facts stay below 2**63.
- Visit/protection need their supported positive result bodies. A legacy
  headerless result may not be borrowed under another action's title.
  Protection also needs its seal delta and a bounded expense when that expense
  is the positive proof.
- Moon status needs the actual moon partner, unique awakened resonance and an
  explicit absolute affinity. An awakening title needs its own acknowledgement
  and complete stat panel. Neither title alone establishes these facts.
- Refusals need their own context; examples and unconfirmed mentions cannot
  mark an action done. A remaining-wait clause must be complete, unique and
  associated with exactly one action. Nominal cooldown text in panel/help
  footers is not an operation result. Mixed positive/refusal results remain
  unknown.
- Keep R73/R74 native bot/chat/command/account ownership and transaction
  boundaries. Unknown replies retain pending/held work and shared pending
  roots; they do not spend a daily slot, invent a CD, change affinity or
  authorize another copy of the mutation.
- An incomplete or terminal-type-changing edit cannot overwrite an existing
  accounted result of the same command. Supported same-type corrections and
  cosmetic edits retain existing chronology/delta handling. This is not an
  implementation of arbitrary post-consumption correction.
- Save failure preserves prior pending, affinity and evidence. SQLite/JSON
  reload and paused local-log replay preserve ownership and exactly-once
  completion. No retry controller, schema field or runtime flag was added.
- Remove obsolete loose owner-success branches and a stale mock-only log
  import. Financial banner/strip results still delegate to R65 before this
  parser; they do not acquire a second accounting path.

## Verification

- 112 additional tests in tests/test_wanxin_reply_contract.py.
- Focused: 3579 passed, 62 subtests; 61.99 seconds.
  /tmp/xiuxian-r97-focused-20260912.xml
- Configured/fatal Ruff, compileall, dependency and whitespace checks passed.
  An additional unused-import check found the inherited console_log import;
  it and its sole test patch were removed before final regression.
- Final isolated full regression: 11058 passed, 1275 subtests; 160.29 seconds.
  /tmp/xiuxian-r97-full-first-20260912.xml
  JUnit: 12333 cases including subtests, no failures/errors/skips.
  All test sessions completed. Only documentation changed after this run.

## Remaining Gates

No real moon-join success body has been captured. Its old heading-only success
shortcut is removed; capture and replay authoritative success text before
claiming this action works or approving its rollout. Existing switches are
unchanged. Known refusal handling remains available.

Commission publication/cancellation/acceptance and identification text
semantics still need their separate review. Do not call all nonfinancial
Wanxin behavior complete on the basis of this owner-action contract.

General affinity chronology across other writers, unowned legacy/manual
operations, R65 retention/capacity, R74 forced-stop migration, R07 durability,
production identity-bound recalibration and whole-project Final Review remain
open. Passing offline tests does not establish production recovery.
